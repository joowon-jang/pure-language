#!/usr/bin/env python3
"""pure-language hook for Claude Code and Codex.

    run.py prompt   UserPromptSubmit: pick the turn language, inject the guard
    run.py stop     Stop: check the finished answer; if other languages leaked in,
                    ask the model to restate only those sentences

Neither host can replace an answer in place, so the Stop step adds a short
correction instead. Standard library only; never fails the host (always exit 0).
"""

import hashlib
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import core as E          # noqa: E402
from engine import settings as S      # noqa: E402


def _is_codex(data):
    return "turn_id" in data or bool(os.environ.get("PLUGIN_ROOT"))


def _settings(codex):
    home = os.path.expanduser("~")
    if codex:
        base = os.environ.get("CODEX_HOME") or os.path.join(home, ".codex")
    else:
        base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(home, ".claude")
    return S.load([os.path.join(base, "pure-language.json")]), base


def _data_dir():
    d = (os.environ.get("PLUGIN_DATA") or os.environ.get("CLAUDE_PLUGIN_DATA")
         or os.path.join(os.path.expanduser("~"), ".pure-language"))
    os.makedirs(d, exist_ok=True)
    return d


def _state_file(session):
    h = hashlib.sha1((session or "default").encode("utf-8")).hexdigest()[:16]
    return os.path.join(_data_dir(), "session-%s.json" % h)


def _load(session):
    try:
        with open(_state_file(session), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(session, data):
    path = _state_file(session)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)
    # prune sessions idle for more than a week
    cutoff = time.time() - 7 * 86400
    for name in os.listdir(_data_dir()):
        p = os.path.join(_data_dir(), name)
        if name.startswith("session-") and os.path.getmtime(p) < cutoff:
            try:
                os.remove(p)
            except OSError:
                pass


def _emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()


def on_prompt(data, codex):
    cfg, _ = _settings(codex)
    session = data.get("session_id") or "default"
    msg = data.get("prompt") or ""
    st = _load(session)
    memo = {"sticky": st.get("sticky", {}), "last": st.get("last", {})}
    target, allowed, skip = E.resolve_turn(memo, session, msg, default=cfg["default_language"])
    st.update(sticky=memo["sticky"], last=memo["last"], turn={
        "target": target, "allowed": sorted(allowed), "skip": skip, "text": msg[:4000],
        "user": sorted({w.lower() for w in re.findall(r"[A-Za-z]{2,}", msg)})[:400],
        "user_cjk": sorted(set(E._CJK_RE.findall(msg)) | set(E._HANGUL_RUN.findall(msg)))[:400],
    })
    _save(session, st)
    full = bool(st.pop("leaked", False))
    _save(session, st)
    if not skip:
        _emit({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                      "additionalContext": E.guard_for(target, frozenset({target} | E.requested_languages(msg)),
                                                                         full=full)}})


def _feedback(target, hits):
    name = E.LANG_NAMES.get(target, target)
    whole = [h for h in hits if str(h[0]).startswith("whole")]
    if whole:
        other = E.LANG_NAMES.get(whole[0][1], whole[0][1])
        return ("[pure-language] Your last reply was written in %s, but this conversation is in %s. "
                "Repeat the same reply in full, written in %s. Keep code blocks, commands, paths, "
                "proper nouns and acronyms as they are." % (other, name, name))
    words = []
    for h in hits:
        w = str(h[1]).strip()
        if w and w not in words:
            words.append(w)
    name = E.LANG_NAMES.get(target, target)
    shown = ", ".join("`%s`" % w for w in words[:12])
    return ("[pure-language] Your last reply mixed in text that is not %s: %s. "
            "Restate only the sentences that contain it, rewritten entirely in %s, as a short "
            "correction under a one-line heading in %s. Do not repeat the rest of the reply. "
            "Keep code, paths, proper nouns and acronyms as they are; quoted command output or error "
            "messages go in a code block." % (name, shown, name, name))


_TOOL_TYPES = {"tool_result", "function_call_output", "custom_tool_call_output", "exec_command_end",
               "mcp_tool_call_end", "patch_apply_end"}


def _texts(node, out):
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, list):
        for x in node:
            _texts(x, out)
    elif isinstance(node, dict):
        for k in ("content", "output", "text", "stdout", "stderr", "aggregated_output", "result"):
            if k in node:
                _texts(node[k], out)


def _find_tool(node, out):
    if isinstance(node, dict):
        if node.get("type") in _TOOL_TYPES:
            _texts(node, out)
            return
        for v in node.values():
            _find_tool(v, out)
    elif isinstance(node, list):
        for x in node:
            _find_tool(x, out)


def _turn_tool_output(path, prompt, limit=300_000):
    """Tool / command output since the user's last prompt, from the host transcript
    (Claude Code and Codex both write JSON lines). Best effort: any error -> ""."""
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, os.path.getsize(path) - 4_000_000))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    start = 0
    key = (prompt or "").strip()[:200]
    if key:
        for i in range(len(lines) - 1, -1, -1):
            if key in lines[i] or json.dumps(key, ensure_ascii=False)[1:-1] in lines[i]:
                start = i
                break
    out = []
    for line in lines[start:]:
        try:
            _find_tool(json.loads(line), out)
        except ValueError:
            continue
    return "\n".join(out)[-limit:]


def on_stop(data, codex):
    cfg, base = _settings(codex)
    if data.get("stop_hook_active") or cfg["stop_action"] == "off":
        return _emit({}) if codex else None
    session = data.get("session_id") or "default"
    turn = _load(session).get("turn") or {}
    text = data.get("last_assistant_message") or ""
    if not turn or turn.get("skip") or not text:
        return _emit({}) if codex else None
    target = turn["target"]
    hits = E.leaks(text, set(turn.get("user", [])), set(turn.get("user_cjk", [])), target=target,
                   allowed=frozenset(turn.get("allowed") or [target]), user_text=turn.get("text", ""),
                   context=_turn_tool_output(data.get("transcript_path"), turn.get("text", "")))
    if not hits:
        return _emit({}) if codex else None
    if cfg["log"]:
        E.write_log(os.path.join(base, "pure-language.log"), session,
                    [("[%s] %s" % (target, h[0]),) + tuple(h[1:]) for h in hits])
    # Asking the model to correct itself costs a whole extra turn. Do it for a reply in
    # the wrong language or several leaks; for a stray word or two, only tighten the
    # rule for the next turn.
    st = _load(session)
    st["leaked"] = True
    _save(session, st)
    whole = any(str(h[0]).startswith("whole") for h in hits)
    if not whole and len({str(h[1]) for h in hits}) < cfg["stop_min_hits"]:
        return _emit({}) if codex else None
    msg = _feedback(target, hits)
    if codex:
        _emit({"decision": "block", "reason": msg})
    else:
        _emit({"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": msg}})


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    codex = _is_codex(data)
    try:
        if event == "prompt":
            on_prompt(data, codex)
        elif event == "stop":
            on_stop(data, codex)
    except Exception as exc:  # never break the host
        sys.stderr.write("pure-language: %s\n" % exc)
        if event == "stop" and codex:
            _emit({})
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
