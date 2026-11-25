# Changelog

## 0.1.0

First public release: Hermes Agent plugin, Claude Code and Codex hooks, 54 languages.
Pasted terminal/log material is not treated as a language, and the answer may quote this turn's command and tool output word for word.
A message that is only pasted command output keeps the conversation's language; answers written wholly in the wrong language are rewritten (Hermes) or restated (Claude Code, Codex).
Cost: one-line rule per turn (full rule only after a leak), sentence-level rewrites, shorter rewrite prompt, `stop_min_hits` for Claude Code / Codex.
