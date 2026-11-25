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
| `timeout` | `15` | Hermes: seconds for the rewrite call (3–25; Hermes stops hooks at 30) |
| `max_lines` | `20` | Hermes: most lines sent in one rewrite call |
| `stop_action` | `rewrite` | Claude Code / Codex: `rewrite` asks for a short correction, `off` only injects the guard |
| `log` | `false` | Write leak snippets (about 20 characters around each hit, never whole answers) to a local log |

On Hermes the rewrite call uses the plugin's own auxiliary slot,
`pure_language_rewrite`. It follows your main model until you pin another one
in **Settings › Models › Auxiliary › Language rewrite**, or in `config.yaml`:

```yaml
auxiliary:
  pure_language_rewrite:
    provider: anthropic
    model: claude-haiku-4-5
```

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
