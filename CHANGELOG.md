# Changelog

## 0.2.1

Progress notes between tool calls stay in the answer language: Hermes appends the one-line rule to each tool result (`transform_tool_result`; English output in an English turn is left alone; `tool_reminder: false` turns it off).
Language detection: Korean language commands count only as requests (`영어로 대답해`, `영어로 답해 줘`), not descriptions (`자꾸 영어로 대답하고`); the host's wrapper tags (`[OUT-OF-BAND USER MESSAGE …]`) are ignored, and a message that is only a host notice (`[IMPORTANT: Background process …]`) keeps the previous language.

## 0.2.0

New hosts: OpenClaw, Pi and Oh My Pi, through a dependency-free JavaScript port of the engine (`js/engine.mjs`) that reads the same word tables as the Python engine; `tests/parity.py` keeps the two identical.
Pi / Oh My Pi replace the finished message (`message_end` / `assistant_message`), ask for another pass when that fails, and Oh My Pi hides leaked words while the answer streams. OpenClaw rejects a leaking draft and has the model write it again (`before_agent_finalize`), and cleans outgoing replies.
Every leaking line is now rewritten by a model by default; word tables and Hanja readings are only the fallback (`local_fix: "first"` restores the old order). `stop_min_hits` defaults to 1.
Language detection ignores notices the host appends to the user message (`[System: ...]`) and the plugin's own rule line, and no longer treats a question about an earlier reply (`왜 영어로 대답했지?`) as a language command.
Words right before a sentence-ending period (`legally.`) are caught; file names like `file.txt` are still skipped.

## 0.1.0

First public release: Hermes Agent plugin, Claude Code and Codex hooks, 54 languages.
Pasted terminal/log material is not treated as a language, and the answer may quote this turn's command and tool output word for word.
A message that is only pasted command output keeps the conversation's language; answers written wholly in the wrong language are rewritten (Hermes) or restated (Claude Code, Codex).
Cost: one-line rule per turn (full rule only after a leak), sentence-level rewrites, shorter rewrite prompt, `stop_min_hits` for Claude Code / Codex.
Reliability: hard deadline on the rewrite call and a 10-minute pause after a failure, so Hermes' 30 s hook limit never drops the whole fix; foreign runs woven into Korean sentences are caught even when that language is allowed.
Guard: ask for translated terms with the original once in parentheses (the old "originals in backticks" wording made models keep untranslated terms); pasted-material languages are no longer announced as allowed.
