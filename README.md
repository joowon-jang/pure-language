# pure-language

Keep every answer in **one language**. `pure-language` works out which language
an answer should be in, tells the model up front, and checks the finished answer
for words and characters that leaked in from other languages — the stray
English word in a Korean answer, Chinese characters in a Japanese one, Korean
inside an English reply.

Works with **Hermes Agent**, **Claude Code** and **Codex**. Pure standard-library
Python; no packages to install.

## How the target language is chosen

1. An explicit command in your message wins and stays in force for the session:
   `answer in English`, `영어로 답해줘`, `日本語で答えて`, `用中文回答`. A one-off
   form (`this time`, `이번에는`) applies to that turn only. `다시 한국어로` or
   `answer in Korean` switches back.
2. Otherwise, the language you wrote in. Pasted quotes (`>` lines) and code are
   ignored, and the last line you wrote decides, so pasting a Japanese email and
   asking about it in Korean gives a Korean answer.
3. Very short messages (`ok`, `thanks`) keep the previous turn's language;
   with no history the `default_language` setting applies (`en` by default).

Requests for partial foreign text (`일본어로 메일 초안 써줘`, `translate this into
French`) keep the answer language and **allow** the second language. Languages
of pasted material are allowed too, so quoting them is never "corrected".

54 languages across 22 scripts are detected: Korean, Japanese, Chinese,
English, Spanish, French, German, Portuguese, Italian, Dutch, Swedish, Danish,
Norwegian, Finnish, Polish, Czech, Romanian, Hungarian, Turkish, Indonesian,
Malay, Filipino, Vietnamese, Swahili, Russian, Ukrainian, Bulgarian, Serbian,
Kazakh, Mongolian, Greek, Arabic, Persian, Urdu, Hebrew, Hindi, Marathi,
Nepali, Bengali, Punjabi, Gujarati, Tamil, Telugu, Kannada, Malayalam,
Sinhala, Thai, Lao, Burmese, Khmer, Georgian, Armenian, Amharic, Tibetan.

## What counts as a leak

* Any script that does not belong to the target language (Hangul in a Japanese
  answer, Cyrillic in a Korean one, Han characters in an English one…).
* Han characters outside Japanese use (JIS X 0208/0212/0213 + Jōyō) in a
  Japanese answer, i.e. Simplified-Chinese-only characters.
* Common English words or runs of English words inside a non-English answer,
  and distinctive English words inside other Latin-script languages (cognates
  such as French `important` or German `also` are left alone).

Never touched: code blocks and inline code, URLs and paths, ACRONYMS, proper
nouns, common tool names (`git`, `npm`, `docker`…), and anything you typed
yourself in that message.

Technical material is not language either:

* Pasted terminal sessions, logs and stack traces (shell prompts such as
  `PS C:\>`, `$`, `kd>`, `>>>`, ANSI colour codes, timestamps, `Traceback`…)
  are ignored when choosing the target language, and such lines in an answer
  are left as they are.
* A shell prompt line (`PS C:\> wpr -status`) starts an output block: the lines
  after it count as command output (banners, status text) until a line in
  another script or a question. A message that is only pasted output keeps the
  conversation's language instead of switching to English.
* Command and tool output of the current turn may be quoted word for word:
  a sentence like `결과에 "Access is denied." 라고 나옵니다` is kept. Only
  stretches of three or more words (or ten or more CJK characters, or
  technical strings) count as a quote, so a single word lifted from the output,
  such as a Japanese term from a fetched web page, is still treated as a leak.
  Hermes collects that output through `post_tool_call`; Claude Code and Codex
  read it from the session transcript.

## How the guard is worded

The guard asks for translated terms, with the original added once in
parentheses only when it helps: `내정 취소(`内定取消し`)`. An earlier wording
("put foreign text in backticks") led models to keep source terms instead of
translating them, giving answers full of backticked Japanese the reader could
not read. Languages found in pasted material are tolerated as quotes but are
never announced as "allowed" in the guard, because models read that as
permission to write in them; only languages the user explicitly asks for are
announced.

## Whole answers in the wrong language

If the prose of an answer (code, paths and terminal output aside) is almost
entirely in another language, for example an English reply in a Korean
conversation, the whole answer is treated as one leak. Hermes rewrites every
prose line in one call (code blocks, inline code, links and list structure are
kept); if that call fails, the answer is left untouched rather than covered in
`⚠` marks. Claude Code and Codex ask the model to repeat the reply in the right
language.

## What each host can do

| | Hermes Agent | Claude Code | Codex |
|---|---|---|---|
| Guard injected before the answer | yes (`pre_llm_call`) | yes (`UserPromptSubmit`) | yes (`UserPromptSubmit`) |
| Leak check on the final answer | yes | yes (`Stop`) | yes (`Stop`) |
| How leaks are fixed | the answer is **replaced** by a cleaned copy before it is stored and shown | the model is asked to restate only the affected sentences in a short follow-up | same as Claude Code (continuation prompt) |

Hermes can rewrite an answer in place, so it gets the full pipeline:

1. Korean answers: built-in tables for common leaked words (with particle and
   verb-ending fixes), Hanja → Hangul readings from the Unicode Unihan database
   with the initial-sound rule (두음법칙).
2. Anything the local rules cannot fix: only those lines are rewritten in one
   bounded call to your model, then re-checked; markdown structure (list
   markers, table pipes, inline code, links) must survive or the rewrite is
   discarded.
3. If the call fails or times out, a deterministic fallback is used (reading,
   else a `⚠` marker). The answer is never lost.

## Install

### Hermes Agent

```bash
hermes plugins install joowon-jang/pure-language
hermes plugins enable pure-language
```

### Claude Code

```bash
claude plugin marketplace add joowon-jang/pure-language
claude plugin install pure-language@pure-language
```

### Codex

```bash
codex plugin marketplace add joowon-jang/pure-language
codex plugin add pure-language@pure-language
```

Codex does not run plugin hooks until you trust them: open `/hooks` in the CLI
(or **Settings › Hooks** in the desktop app) and trust both `pure-language`
hooks.

The Claude Code and Codex hooks run `python3`, falling back to `python`;
Python 3.8+ must be on `PATH`.

## Settings

Optional JSON file; every key is optional.

| Host | File |
|---|---|
| Hermes Agent | `$HERMES_HOME/pure-language.json` (default `~/.hermes`) |
| Claude Code | `~/.claude/pure-language.json` |
| Codex | `$CODEX_HOME/pure-language.json` (default `~/.codex`) |

`PURE_LANGUAGE_CONFIG=/path/file.json` overrides all of them.

```json
{
  "default_language": "ko",
  "rewrite": true,
  "hold_stream": false,
  "timeout": 15,
  "max_lines": 20,
  "stop_action": "rewrite",
  "log": false
}
```

| Key | Default | Meaning |
|---|---|---|
| `default_language` | `en` | Language used when a message is too short to detect and there is no earlier turn |
| `rewrite` | `true` | Hermes: rewrite unfixable lines with the model (off = local rules and `⚠` only) |
| `hold_stream` | `false` | Hermes: do not stream; show only the cleaned answer. If the cleaner fails, Hermes shows a fixed notice instead of the answer |
| `timeout` | `15` | Hermes: hard deadline in seconds for the rewrite call (5–20). Hermes cancels a hook after 30 s and then skips it for 60 s, so the plugin stops waiting first, keeps the local fixes, and pauses model rewrites for 10 minutes after a failure or timeout |
| `max_lines` | `20` | Hermes: most lines sent in one rewrite call |
| `stop_action` | `rewrite` | Claude Code / Codex: `rewrite` asks for a short correction, `off` only injects the guard |
| `stop_min_hits` | `3` | Claude Code / Codex: distinct leaked words needed before asking for a correction (which costs an extra turn). Fewer only tighten the rule for the next turn; a reply in the wrong language always triggers it |
| `log` | `false` | Write leak snippets (about 20 characters around each hit, never whole answers) to a local log |

On Hermes the rewrite call uses the plugin's own auxiliary slot,
`pure_language_rewrite`. It follows your main model until you pin another one
on the Models page (**Show auxiliary** → `Language rewrite` → **Change**), with
`hermes config set auxiliary.pure_language_rewrite.model <model>`, or in `config.yaml`:

```yaml
auxiliary:
  pure_language_rewrite:
    provider: anthropic
    model: claude-haiku-4-5
```

## Cost

The plugin is built to add as few tokens as possible:

* **Per turn:** a one-line rule (about 40–100 characters) is added to your
  message. The full rule (about 300 characters) is used only on the turn after a
  leak was found, then it drops back to one line. Hosts keep this text in the
  conversation history, so a short rule saves tokens on every later call.
* **Clean answers:** no model call at all. Korean word tables and Hanja
  readings fix many leaks locally.
* **Leaky answers (Hermes):** only the sentences that leak are sent, not whole
  paragraphs, with a short English instruction and an output budget sized to the
  input. Results are cached.
* **Rewrite model:** any small model is enough. Pin a cheap one to the
  `pure_language_rewrite` slot (see above); `claude-haiku-4-5` handled both
  single-word fixes and whole-answer rewrites in testing.
* **Claude Code / Codex:** a stray word or two does not trigger an extra turn
  (see `stop_min_hits`).

## Disclosures

* **Model calls.** Hermes: when an answer has leaks the local rules cannot fix,
  one extra call to your configured model rewrites those lines (usually 1–5
  seconds and a few hundred tokens). Claude Code / Codex: a leaky answer
  triggers one short follow-up turn. Clean answers cost nothing extra.
* **Network.** None of its own. The only network use is the model call above,
  made by the host with the host's own credentials. The plugin never reads
  credential files.
* **Local files.** Claude Code / Codex keep a small per-session state file
  (target language, allowed languages, the words of your last message) in the
  plugin data directory, or `~/.pure-language/` when the host gives none; files
  idle for 7 days are deleted. Hermes keeps this in memory only. The optional
  log is described above.
* No telemetry, no background processes, no shell commands.

## Development

```bash
python -m unittest discover -s tests      # no model calls
hermes plugins validate .
```

`engine/` is shared by every host; `__init__.py` is the Hermes adapter and
`hooks/run.py` the Claude Code / Codex adapter.

## License

MIT, see [LICENSE](LICENSE). `engine/data/hanja_ko.json` and
`engine/data/ja_kanji.txt` are derived from the Unicode Character Database
(Unihan); see [NOTICE](NOTICE).
