#!/usr/bin/env python3
"""Export the engine's word tables and texts to engine/data/tables.json so the
JavaScript engine (js/engine.mjs) reads exactly the same data as the Python one.

    python tools/export_tables.py        # run after editing tables in engine/core.py
"""
import json
import unicodedata
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine import core as E  # noqa: E402


def main():
    t = {
        "GUARD": E._GUARD,
        "ZH_FUNC": "".join(sorted(E._ZH_FUNC)),
        "PUNCT_MAP": E._PUNCT_MAP,
        "WORDS": E._WORDS,
        "LATIN_ALLOW": sorted(E._LATIN_ALLOW),
        "PROPER_COLLIDE": sorted(E._PROPER_COLLIDE),
        "LATIN_WORDS": E._LATIN_WORDS,
        "LATIN_VERB_STEMS": sorted(E._LATIN_VERB_STEMS),
        "LATIN_COMMON": sorted(E._LATIN_COMMON),
        "PAIR": E._PAIR,
        "LANGS": {k: list(v) for k, v in E._LANGS.items()},
        "SCRIPT_RANGES": E._SCRIPT_RANGES,
        "SCRIPT_DEFAULT": E._SCRIPT_DEFAULT,
        "EN_DISTINCT": sorted(E._EN_DISTINCT),
        "EN_COLLIDE": {k: sorted(v) for k, v in E._EN_COLLIDE.items()},
        "GUARDS": E._GUARDS,
        "GUARDS_SHORT": E._GUARDS_SHORT,
        "GUARD_SHORT_GENERIC": E._GUARD_SHORT_GENERIC,
        "ALLOW_SHORT": E._ALLOW_SHORT,
        "GUARD_GENERIC": E._GUARD_GENERIC,
        "ALLOW_NOTE": E._ALLOW_NOTE,
        "PHRASE_ALLOW": sorted(E._PHRASE_ALLOW),
        "TARGET_CHARS": E._TARGET_CHARS,
    }
    path = os.path.join(ROOT, "engine", "data", "tables.json")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        # keep order: ties pick the first key; escape invisible characters (U+FEFF, ZWJ, ...)
        out = json.dumps(t, ensure_ascii=False, indent=1)
        out = "".join(f"\\u{ord(c):04x}" if unicodedata.category(c) in ("Cf", "Cc", "Co", "Cs", "Zl", "Zp")
                      and c not in "\n" else c for c in out)
        fh.write(out + "\n")
    print("wrote", path)


if __name__ == "__main__":
    main()
