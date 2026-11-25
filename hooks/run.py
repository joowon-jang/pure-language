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
    if not skip:
        _emit({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                      "additionalContext": E.guard_for(target, allowed)}})


def _feedback(target, hits):
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
            "Keep code, paths, proper nouns and acronyms as they are." % (name, shown, name, name))


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
                   allowed=frozenset(turn.get("allowed") or [target]), user_text=turn.get("text", ""))
    if not hits:
        return _emit({}) if codex else None
    if cfg["log"]:
        E.write_log(os.path.join(base, "pure-language.log"), session,
                    [("[%s] %s" % (target, h[0]),) + tuple(h[1:]) for h in hits])
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
