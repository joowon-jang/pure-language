"""pure-language for Hermes Agent: keep every answer in one language.

The target language comes from an explicit command in the user's message, else the
language the user wrote in, else ``default_language``. ``pre_llm_call`` injects a
short guard in that language; ``transform_llm_output`` cleans the final answer
before it is stored and delivered. Lines the local rules cannot fix are rewritten
in one bounded call through ``ctx.llm`` on the plugin's own auxiliary slot
``pure_language_rewrite`` (defaults to the user's main model).

Settings: ``$HERMES_HOME/pure-language.json`` (see README). Optional log:
``$HERMES_HOME/logs/pure-language.log`` (off by default; snippets only).
"""

import logging
import os
import re
import threading
import time

from .engine import core as E
from .engine import settings as S

log = logging.getLogger(__name__)
_TASK = "pure_language_rewrite"


def _hermes_home():
    """The active profile's home, resolved when a hook runs: one gateway process can
    serve several profiles, so an import-time read would pin the launching one."""
    try:
        from hermes_constants import get_hermes_home
        home = get_hermes_home()
        if home:
            return str(home)
    except Exception:
        pass
    home = os.environ.get("HERMES_HOME")
    if home:
        return home
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return os.path.join(os.environ["LOCALAPPDATA"], "hermes")
    return os.path.join(os.path.expanduser("~"), ".hermes")


class _Settings:
    """Settings of the active profile (``<home>/pure-language.json``), reloaded when the
    home or the file changes. Values assigned here (tests) override the file."""

    def __init__(self):
        self._cache = {}       # path -> (mtime, cfg)
        self._override = {}

    def _current(self):
        path = os.path.join(_hermes_home(), "pure-language.json")
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        hit = self._cache.get(path)
        if hit is None or hit[0] != mtime:
            if len(self._cache) > 32:
                self._cache.clear()
            hit = self._cache[path] = (mtime, S.load([path]))
        return hit[1]

    def __getitem__(self, key):
        return self._override[key] if key in self._override else self._current()[key]

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __setitem__(self, key, value):
        self._override[key] = value


_CFG = _Settings()

_CTX = None
_TASK_OK = False
_MEMO = {}       # sticky / last language per session
_STATE = {}      # per-turn state recorded by pre_llm_call
_TOOLS = {}      # tool / command output of the running turn (the answer may quote it)
_TOOL_MAX = 200_000


def _log_path():
    return os.path.join(_hermes_home(), "logs", "pure-language.log") if _CFG["log"] else None


_BREAKER = {"until": 0.0}   # model rewrites paused after a failure (seconds since epoch)
_PAUSE = 600


def _rewriter(lines, target, allowed, sample):
    """Bounded model rewrite. Hermes cancels a hook that runs past 30 s and then skips
    it for 60 s, which would deliver the raw answer with no fix at all. So the call
    gets a hard deadline in a worker thread, and after a failure or timeout model
    rewrites pause for 10 minutes (local fixes and marks still apply)."""
    if _CTX is None or not getattr(_CTX, "llm", None) or time.time() < _BREAKER["until"]:
        return None
    budget = min(20, max(5, _CFG["timeout"]))
    box = {}

    def work():
        try:
            box["v"] = _call_model(lines, target, allowed, sample)
        except Exception as exc:  # reported below
            box["e"] = exc
    th = threading.Thread(target=work, name="pure-language-rewrite", daemon=True)
    th.start()
    th.join(budget)
    if th.is_alive() or "e" in box:
        _BREAKER["until"] = time.time() + _PAUSE
        log.warning("pure-language: rewrite %s; model rewrites paused for %d s",
                    "timed out after %ds" % budget if th.is_alive() else "failed: %s" % box["e"], _PAUSE)
        return None
    return box.get("v")


def _call_model(lines, target, allowed, sample):
    kwargs = {"task": _TASK} if _TASK_OK else {}
    result = _CTX.llm.complete_structured(
        instructions=E.rewrite_instructions(target, allowed, sample),
        input=[{"type": "text", "text": E.number_lines(lines)}],
        json_schema=E.REWRITE_SCHEMA,
        schema_name="pure_language.lines",
        purpose="pure-language.rewrite",
        temperature=0.0,
        max_tokens=E.rewrite_max_tokens(lines),
        # a whole answer in the wrong language means many lines: allow more time (host cap 30 s)
        timeout=min(20, max(5, _CFG["timeout"])),
        **kwargs,
    )
    return E.parse_rewrite(getattr(result, "parsed", None), len(lines))


def _on_pre_llm(session_id="", user_message="", turn_id=None, **kwargs):
    key = session_id or "default"
    msg = user_message or ""
    target, allowed, skip = E.resolve_turn(_MEMO, key, msg, default=_CFG["default_language"])
    # words the user typed anywhere in this session (gufo, product names) are theirs to use
    seen = _MEMO.setdefault("user_words", {})
    words = seen.setdefault(key, set())
    if len(words) < 2000:
        words.update(w.lower() for w in re.findall(r"[A-Za-z]{2,}", E.strip_host_notes(msg)))
    if len(seen) > 200:
        for k in list(seen)[:-100]:
            seen.pop(k, None)
    st = {"skip": skip, "target": target, "allowed": allowed, "text": msg, "at": time.time(),
          "user": set(words),
          "user_cjk": set(E._CJK_RE.findall(msg)) | set(E._HANGUL_RUN.findall(msg))}
    _STATE[("turn", turn_id) if turn_id else ("session", key)] = st
    if len(_STATE) > 200:
        for k in list(_STATE)[:-100]:
            _STATE.pop(k, None)
    # progress notes of earlier turns in the wrong language: the model copies its own habit
    st["note_leak"] = not skip and _history_notes_off(kwargs.get("conversation_history"), target, allowed)
    # the long rule text only on the turn after a leak; a one-line reminder otherwise
    full = _MEMO.setdefault("leaked", {}).pop(key, False) or st["note_leak"]
    return None if skip else E.guard_for(target, frozenset({target} | E.requested_languages(msg)), full=full)


def _on_post_tool(tool_name="", result=None, session_id="", turn_id=None, **kwargs):
    """Remember this turn's tool output so quoting it word for word is not a leak."""
    if result is None:
        return None
    text = result if isinstance(result, str) else str(result)
    key = ("turn", turn_id) if turn_id else ("session", session_id or "default")
    buf = _TOOLS.get(key, "")
    if len(buf) < _TOOL_MAX:
        _TOOLS[key] = (buf + "\n" + text)[:_TOOL_MAX]
    if len(_TOOLS) > 50:
        for k in list(_TOOLS)[:-25]:
            _TOOLS.pop(k, None)
    return None


def _peek_state(session_id, turn_id):
    if turn_id and ("turn", turn_id) in _STATE:
        return _STATE[("turn", turn_id)]
    st = _STATE.get(("session", session_id or "default"))
    return st if st is not None and time.time() - st["at"] < 1800 else None


_OTHER_SCRIPT_RE = re.compile(r"[^\x00-\u024F\u1E00-\u1EFF\u2000-\u2BFF\uFE00-\uFE0F\uFF00-\uFFEF]")


def _history_notes_off(history, target, allowed, look_back=12):
    """True when one of the recent progress notes (assistant text beside a tool call) is in
    another language."""
    if not isinstance(history, list):
        return False
    seen = 0
    for msg in reversed(history):
        if not isinstance(msg, dict) or msg.get("role") != "assistant" or not msg.get("tool_calls"):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        if isinstance(content, str) and content.strip():
            if E.note_off_language(content, target, allowed):
                return True
            seen += 1
            if seen >= look_back:
                break
    return False


def _on_interim(text="", session_id="", turn_id="", **kwargs):
    """A progress note came out; flag it when it is in the wrong language so the next tool
    result carries a correction."""
    st = _peek_state(session_id, turn_id or None)
    if not st or st["skip"] or not isinstance(text, str):
        return None
    if E.note_off_language(text, st["target"], st["allowed"]):
        st["note_leak"] = True
        st["note_fix"] = True   # the next tool result says so explicitly
        _MEMO.setdefault("leaked", {})[session_id or "default"] = True
    return None


def _on_tool_result(tool_name="", result=None, session_id="", turn_id=None, **kwargs):
    """Remind the model of the answer language right after each tool result.

    Progress notes between tool calls are written right after the model reads tool output
    (usually English), which is when it drifts; the turn's guard sits far above by then."""
    if not isinstance(result, str) or not result.strip():
        return None
    st = _peek_state(session_id, turn_id)
    if not st or st["skip"]:
        return None
    target = st["target"]
    if target == "en" and not _OTHER_SCRIPT_RE.search(result):
        return None   # English output in an English turn needs no reminder
    if st.pop("note_fix", False):
        st["note_told"] = True
        rule = E.note_feedback(target)
    elif st.get("note_leak") and not st.get("note_told"):
        st["note_told"] = True   # earlier turns' notes, not this one
        rule = E.note_feedback(target, earlier=True)
    else:
        rule = E.guard_for(target, frozenset({target} | E.requested_languages(st["text"])), full=False)
    return result if not rule else result.rstrip() + "\n\n" + rule


# ------------------------------------------------- progress notes the model sees again
_NOTE_CACHE = {}       # (target, note) -> rewritten note
_NOTE_BATCH = 8        # most notes rewritten per request (newest first)


def _note_slots(items):
    """(holder, key, text) for the text of each assistant message that came with a tool call,
    in the three request shapes: chat ``messages``, Anthropic ``messages`` (content blocks)
    and Responses ``input`` (message item followed by a function_call item)."""
    slots = []
    for n, msg in enumerate(items):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        blocks = content if isinstance(content, list) else []
        with_tool = bool(msg.get("tool_calls")) or any(
            isinstance(b, dict) and b.get("type") in ("tool_use", "function_call") for b in blocks)
        nxt = items[n + 1] if n + 1 < len(items) else None
        with_tool = with_tool or (isinstance(nxt, dict) and nxt.get("type") == "function_call")
        if not with_tool:
            continue
        if isinstance(content, str):
            slots.append((msg, "content", content))
        for b in blocks:
            if isinstance(b, dict) and b.get("type") in ("text", "output_text") and isinstance(b.get("text"), str):
                slots.append((b, "text", b["text"]))
    return slots


def _on_llm_request(request=None, session_id="", turn_id="", **kwargs):
    """Show the model its earlier progress notes in the answer language. A note written in
    another language stays in the context and the model keeps copying it; the stored
    transcript is left as it was."""
    st = _peek_state(session_id, turn_id or None)
    if not st or st["skip"] or not isinstance(request, dict):
        return None
    items = request.get("messages") if isinstance(request.get("messages"), list) else request.get("input")
    if not isinstance(items, list):
        return None
    target, allowed = st["target"], st["allowed"]
    bad = [(h, k, t) for h, k, t in _note_slots(items) if t.strip() and E.note_off_language(t, target, allowed)]
    if not bad:
        return None
    todo = []
    for _, _, t in reversed(bad):
        if (target, t) not in _NOTE_CACHE and t not in todo:
            todo.append(t)
        if len(todo) >= _NOTE_BATCH:
            break
    if todo and _CFG["rewrite"]:
        got = _rewriter([t.replace("\n", " ") for t in todo], target, allowed, st["text"]) or []
        for t, g in zip(todo, got):
            if isinstance(g, str) and g.strip() and not E.note_off_language(g, target, allowed):
                if len(_NOTE_CACHE) > 2000:
                    _NOTE_CACHE.pop(next(iter(_NOTE_CACHE)))
                _NOTE_CACHE[(target, t)] = g.strip()
    changed = 0
    for holder, key, t in bad:
        fixed = _NOTE_CACHE.get((target, t))
        if fixed:
            holder[key] = fixed
            changed += 1
    if not changed:
        return None
    return {"request": request, "source": "pure-language",
            "reason": "%d progress note(s) shown in %s" % (changed, target)}


def _take_state(session_id, turn_id):
    """Only the turn that ran pre_llm_call is cleaned. Background agents sharing the
    session (memory/skill review forks, compression) fire no pre_llm_call for their
    own turn, so their output is left alone."""
    if turn_id:
        return _STATE.pop(("turn", turn_id), None)
    st = _STATE.pop(("session", session_id or "default"), None)
    return st if st is not None and time.time() - st["at"] < 1800 else None


def _on_transform(response_text="", session_id="", turn_id=None, **kwargs):
    st = _take_state(session_id, turn_id)
    tools = _TOOLS.pop(("turn", turn_id) if turn_id else ("session", session_id or "default"), "")
    if st is None or st["skip"] or not response_text:
        return None
    try:
        text, hits = E.clean(response_text, st["user"], st["user_cjk"], target=st["target"],
                             allowed=st["allowed"] or frozenset({st["target"]}), user_text=st["text"],
                             rewriter=_rewriter if _CFG["rewrite"] else None,
                             max_lines=_CFG["max_lines"], context=tools,
                             local_first=_CFG.get("local_fix") == "first", mark=bool(_CFG.get("mark")))
    except Exception:  # never lose the answer because of the cleaner
        log.warning("pure-language: cleaner failed", exc_info=True)
        return None
    if any(not str(x[0]).startswith("rewritten") for x in hits):
        leaked = _MEMO.setdefault("leaked", {})
        leaked[session_id or "default"] = True
        if len(leaked) > 200:
            for k in list(leaked)[:-100]:
                leaked.pop(k, None)
    if text == response_text:
        return None
    E.write_log(_log_path(), session_id, [("[%s] %s" % (st["target"], h[0]),) + tuple(h[1:]) for h in hits])
    log.info("pure-language: %d fix(es) in final answer (target=%s)", len(hits), st["target"])
    return text


def register(ctx):
    global _CTX, _TASK_OK
    _CTX = ctx
    try:
        ctx.register_auxiliary_task(
            _TASK, display_name="Language rewrite",
            description="pure-language: rewrite only the lines that mix in another language")
        _TASK_OK = True
    except Exception as exc:  # older hosts without auxiliary tasks
        log.info("pure-language: auxiliary task not registered: %s", exc)
    ctx.register_hook("pre_llm_call", _on_pre_llm)
    ctx.register_hook("post_tool_call", _on_post_tool)
    if _CFG["tool_reminder"]:
        ctx.register_hook("transform_tool_result", _on_tool_result)
        ctx.register_hook("on_interim_message", _on_interim)
        if hasattr(ctx, "register_middleware"):
            ctx.register_middleware("llm_request", _on_llm_request)
    if _CFG["hold_stream"]:
        ctx.register_hook("transform_llm_output", _on_transform, hold_stream=True)
    else:
        ctx.register_hook("transform_llm_output", _on_transform)
