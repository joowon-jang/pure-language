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
        cfg["timeout"] = max(3, min(25, int(cfg["timeout"])))
        cfg["max_lines"] = max(1, int(cfg["max_lines"]))
    except (TypeError, ValueError):
        cfg["timeout"], cfg["max_lines"] = DEFAULTS["timeout"], DEFAULTS["max_lines"]
    if not isinstance(cfg["default_language"], str) or not cfg["default_language"]:
        cfg["default_language"] = "en"
    return cfg
