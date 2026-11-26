"""Settings shared by all adapters. A user file overrides the defaults; nothing is
required. Unknown keys are ignored."""

import json
import os

DEFAULTS = {
    "default_language": "en",   # used when a message is too short to detect and no earlier turn exists
    "rewrite": True,            # Hermes: rewrite unfixable lines with the user's model
    "hold_stream": False,       # Hermes: hide streaming, show only the cleaned answer
    "timeout": 15,              # Hermes: seconds for the rewrite call (host stops hooks at 30)
    "max_lines": 20,            # Hermes: most lines per rewrite call
    "stop_action": "rewrite",   # Claude Code / Codex: "rewrite" asks the model to restate leaked
                                #   sentences, "off" only injects the guard
    "stop_min_hits": 1,         # Claude Code / Codex / OpenClaw: distinct leaked words before asking
                                #   for a correction (an extra turn); a wrong-language reply always does
    "local_fix": "fallback",    # "fallback": the model rewrites every leaking line, word tables and
                                #   Hanja readings only when it cannot; "first": rules first (cheaper)
    "log": False,               # write hit snippets (not whole answers) to a local log file
}


def load(paths):
    """Merge the JSON files in ``paths`` (later wins) over DEFAULTS."""
    cfg = dict(DEFAULTS)
    env = os.environ.get("PURE_LANGUAGE_CONFIG")
    for path in list(paths) + ([env] if env else []):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    try:
        cfg["timeout"] = max(5, min(20, int(cfg["timeout"])))
        cfg["max_lines"] = max(1, int(cfg["max_lines"]))
        cfg["stop_min_hits"] = max(1, int(cfg["stop_min_hits"]))
    except (TypeError, ValueError):
        for k in ("timeout", "max_lines", "stop_min_hits"):
            cfg[k] = DEFAULTS[k]
    if cfg["local_fix"] not in ("fallback", "first"):
        cfg["local_fix"] = "fallback"
    if not isinstance(cfg["default_language"], str) or not cfg["default_language"]:
        cfg["default_language"] = "en"
    return cfg
