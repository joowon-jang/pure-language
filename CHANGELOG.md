# Changelog

## 0.2.6

File paths, URLs and Hermes `MEDIA:` lines are no longer changed. A Windows path such as `C:\Users\x\【KOREC専用】履歴書.xlsx` outside backticks had its Japanese characters and brackets rewritten (`[KOREC전용]이력서`), so the file link pointed at a file that does not exist. Paths (Windows drive, UNC, POSIX, `~/`, `./`), URLs and the rest of a `MEDIA:` line are masked before cleaning, keeping their length so the "Hangul within 40 characters" test reads the line as before, and a model rewrite that alters a path is rejected. Both engines; on 1,240 past answers nothing else changed.
Hermes: with `"hold_stream": true`, progress notes are cleaned before they are shown when Hermes has the `transform_interim_message` hook (a held turn used to drop them). A note in another language is rewritten by the model; one that stays foreign is not shown. On older Hermes the hook is not registered and held turns keep dropping notes.

## 0.2.5

Attached files no longer decide the answer language. Hermes appends an English `--- Attached Context ---` block (and `--- Context Warnings ---`) below the typed text for every `@file:` reference; the language was read from the last lines, so a Korean request with an attachment got an English rule. Everything from that marker to the end is now dropped before the language is decided, in both engines.

## 0.2.4

Host-notice patterns run on every user message and are now linear: the wrapper-tag pattern no longer lets its word and separator parts overlap (a 200-character `[A-A-A…` line took exponential time before) and its tail is capped at 200 characters; a multi-line notice is matched line by line for at most 40 lines instead of `.*?` over the rest of the message. Timing tests cover both engines.
Hermes: settings are read from the active profile's home when a hook runs (`hermes_constants.get_hermes_home()`), so one gateway serving several profiles uses each profile's `pure-language.json`.

## 0.2.3

Unfixed leaks are left as written by default; `"mark": true` restores the ⚠ marks, now placed after the original text instead of replacing it.
Words the user typed anywhere in the session (product and process names such as `gufo`) stay allowed in later turns, not only in the turn that used them.
Hermes: earlier progress notes written in another language are shown to the model in the answer language (`llm_request` middleware; one bounded rewrite call per request for up to 8 notes, cached), so the model stops copying them. The stored transcript is unchanged.
The correction for earlier turns' notes no longer claims the note just written was in the wrong language.

## 0.2.2

Hermes watches each progress note (`on_interim_message`); when one comes out in another language, the next tool result carries an explicit correction instead of the plain rule. When earlier progress notes in the conversation are in the wrong language (the model copies its own habit), the turn starts with the full rule and the first tool result carries the correction.
A message of a few Latin-script words (`continue`, `ok`, `go on`) keeps the conversation's language, or the configured default right after a restart, instead of switching to English.

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
