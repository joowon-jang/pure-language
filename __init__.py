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
    home = os.environ.get("HERMES_HOME")
    if home:
        return home
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return os.path.join(os.environ["LOCALAPPDATA"], "hermes")
    return os.path.join(os.path.expanduser("~"), ".hermes")


_CFG = S.load([os.path.join(_hermes_home(), "pure-language.json")])
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
    st = {"skip": skip, "target": target, "allowed": allowed, "text": msg, "at": time.time(),
          "user": {w.lower() for w in re.findall(r"[A-Za-z]{2,}", msg)},
          "user_cjk": set(E._CJK_RE.findall(msg)) | set(E._HANGUL_RUN.findall(msg))}
    _STATE[("turn", turn_id) if turn_id else ("session", key)] = st
    if len(_STATE) > 200:
        for k in list(_STATE)[:-100]:
            _STATE.pop(k, None)
    # the long rule text only on the turn after a leak; a one-line reminder otherwise
    full = _MEMO.setdefault("leaked", {}).pop(key, False)
    return None if skip else E.guard_for(target, allowed, full=full)


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
                             max_lines=_CFG["max_lines"], context=tools)
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
    if _CFG["hold_stream"]:
        ctx.register_hook("transform_llm_output", _on_transform, hold_stream=True)
    else:
        ctx.register_hook("transform_llm_output", _on_transform)
