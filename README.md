# pure-language

Keep every answer in **one language**. `pure-language` works out which language
an answer should be in, tells the model up front, and checks the finished answer
for words and characters that leaked in from other languages — the stray
English word in a Korean answer, Chinese characters in a Japanese one, Korean
inside an English reply.

Works with **Hermes Agent**, **Claude Code**, **Codex**, **OpenClaw**, **Pi** and
**Oh My Pi**. The Python engine uses only the standard library; the JavaScript
engine (for OpenClaw, Pi and Oh My Pi) has no dependencies. Both read the same
word tables and are checked against each other (`tests/parity.py`).

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

Only requests count as commands: `영어로 대답해` switches, while `왜 영어로
대답했지?`, `자꾸 영어로 대답하고` or `why did you answer in English?` talk about
earlier replies and do not. Notices and wrapper tags the host adds to your
message (`[System: ...]`, `[OUT-OF-BAND USER MESSAGE …]`, the `--- Attached Context ---` block
below an `@file:` reference) and the plugin's own
rule line are ignored; a message that is only a host notice (a background job
finished) keeps the previous language, and so does a message of a few English
words (`continue`, `ok`).

## What counts as a leak

* Any script that does not belong to the target language (Hangul in a Japanese
  answer, Cyrillic in a Korean one, Han characters in an English one…).
* Han characters outside Japanese use (JIS X 0208/0212/0213 + Jōyō) in a
  Japanese answer, i.e. Simplified-Chinese-only characters.
* Common English words or runs of English words inside a non-English answer,
  and distinctive English words inside other Latin-script languages (cognates
  such as French `important` or German `also` are left alone).

Never touched: code blocks and inline code, URLs, file paths (Windows, UNC,
POSIX, `~/`, `./`) and Hermes `MEDIA:` lines, ACRONYMS, proper
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
conversation, the whole answer is treated as one leak. Hermes, Pi and Oh My Pi
rewrite every prose line in one call (code blocks, inline code, links and list structure are
kept); if that call fails, the answer is left untouched rather than covered in
`⚠` marks. OpenClaw rejects the draft and has the model write it again; Claude
Code and Codex ask the model to repeat the reply in the right language.

## What each host can do

| | Guard before the answer | How a leaking answer is fixed |
|---|---|---|
| Hermes Agent | `pre_llm_call` | the answer is **replaced** by a rewritten copy (`transform_llm_output`) |
| Oh My Pi | hidden message (`before_agent_start`) | the finished message is **replaced** (`assistant_message`); while it streams, a display renderer hides leaked words |
| Pi | hidden message (`before_agent_start`) | the finished message is **replaced** (`message_end`); the screen redraws with the fix |
| OpenClaw | `before_prompt_build` | the draft is rejected and the model writes the whole reply again (`before_agent_finalize` → revise); outgoing replies are cleaned too (`reply_payload_sending`, `message_sending`) |
| Claude Code / Codex | `UserPromptSubmit` | the model is asked to restate the affected sentences (`Stop`) |

By default every leaking line is **rewritten by a model**. Word tables and Hanja
readings can read awkwardly (Japanese `書類選考` read as Korean sounds gives a
wrong term), so they are only the fallback when the model call fails or times
out. `"local_fix": "first"` restores the cheaper order (rules first, the model
only for what they cannot fix). Markdown structure (list markers, table pipes,
inline code, links) must survive a rewrite or it is discarded. The answer is
never lost.

### Host limits

* **Pi:** text that has already streamed stays on screen until the reply is
  finished; Pi has no hook for drawing answer text. The final screen shows the
  fixed reply.
* **Oh My Pi:** the streaming display needs the `extensionRenderers.assistantText`
  setting (`omp config set extensionRenderers.assistantText true`).
* **Hermes progress notes** (text between tool calls) appear while they are
  written, so a note in the wrong language is corrected only afterwards: the
  next tool result tells the model, and later requests show it the rewritten
  note. With `"hold_stream": true` on a Hermes that has the
  `transform_interim_message` hook, each note is cleaned before it is shown and
  one that stays in another language is not shown; replies then arrive whole
  instead of streaming.
* **OpenClaw with a Claude CLI login** (`claude-cli` runtime): OpenClaw runs the
  turn inside Claude Code and fires no finalize or transcript hook, so only the
  guard and outgoing replies are handled; the stored history keeps the raw
  reply. The embedded runtime (API-key providers) gets the full pipeline.
* **OpenClaw on Windows:** reloading a plugin in a running Gateway can fail with
  `EPERM` while OpenClaw links its own package; restart the Gateway instead.

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
hooks. The Claude Code and Codex hooks run `python3`, falling back to `python`;
Python 3.8+ must be on `PATH`.

### Oh My Pi

```bash
omp plugin install github:joowon-jang/pure-language
omp config set extensionRenderers.assistantText true   # hide leaks while streaming
```

### Pi

```bash
pi install git:github.com/joowon-jang/pure-language
```

### OpenClaw

```bash
openclaw plugins install git:github.com/joowon-jang/pure-language --force --accept-capabilities
openclaw config set plugins.entries.pure-language.hooks.allowConversationAccess true
```

`--force` confirms a source outside ClawHub; the plugin is enabled on install.
It declares conversation read access, and OpenClaw needs the last setting to
run conversation hooks for plugins it does not bundle. Restart the Gateway.

## Settings

Optional JSON file; every key is optional.

| Host | File |
|---|---|
| Hermes Agent | `$HERMES_HOME/pure-language.json` (default `~/.hermes`) |
| Claude Code | `~/.claude/pure-language.json` |
| Codex | `$CODEX_HOME/pure-language.json` (default `~/.codex`) |
| Oh My Pi | `~/.omp/agent/pure-language.json` |
| Pi | `~/.pi/agent/pure-language.json` (or `$PI_CODING_AGENT_DIR`) |
| OpenClaw | `plugins.entries.pure-language.config` in `openclaw.json`, or `~/.openclaw/pure-language.json` |

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
| `hold_stream` | `false` | Hermes: do not stream; show only the cleaned answer. If the cleaner fails, Hermes shows a fixed notice instead of the answer. On a Hermes with `transform_interim_message`, progress notes are cleaned before they are shown (a note that stays in another language is hidden); without it they are hidden |
| `timeout` | `15` | Hard deadline in seconds for the rewrite call (5–20). Hermes cancels a hook after 30 s and then skips it for 60 s, so the plugin stops waiting first, keeps the local fixes, and pauses model rewrites for 10 minutes after a failure or timeout |
| `max_lines` | `20` | Hermes: most lines sent in one rewrite call |
| `stop_action` | `rewrite` | Claude Code / Codex: `rewrite` asks for a short correction, `off` only injects the guard |
| `stop_min_hits` | `1` | Claude Code / Codex / OpenClaw / Pi: distinct leaked words needed before asking the model for a correction (an extra turn). Raise it to save cost; fewer only tighten the rule for the next turn. A reply in the wrong language always triggers it |
| `mark` | `false` | Put ⚠ after a leak that neither the model nor the rules could fix (default: leave it as written) |
| `tool_reminder` | `true` | Hermes: add the one-line rule after each tool result, so progress notes written after reading (usually English) tool output stay in the answer language. English output in an English turn is left alone. When a progress note comes out in another language (or earlier notes in the conversation did), the next tool result says so explicitly, and later requests show the model that note in the answer language (`llm_request` middleware) so it does not copy it |
| `local_fix` | `fallback` | `fallback`: the model rewrites every leaking line, word tables and Hanja readings only when it cannot. `first`: rules first, the model only for what they cannot fix (cheaper, can read awkwardly) |
| `rewrite_model` | (empty) | Oh My Pi / Pi: model for rewrites as `provider/id` (default: Oh My Pi's `@smol` role, then the session model). OpenClaw: model ref for `api.runtime.llm.complete`; choosing one needs `plugins.entries.pure-language.llm.allowModelOverride: true` |
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

* **Per turn:** a one-line rule (about 40–100 characters) is added. The full rule
  (about 300 characters) is used only on the turn after a leak was found.
* **Clean answers:** no model call at all.
* **Leaky answers:** only the lines (long lines: only the sentences) that leak
  are sent, with a short instruction and an output budget sized to the input.
  Results are cached. OpenClaw and Claude Code / Codex use the session model for
  one extra pass instead.
* **Rewrite model:** any small model is enough; `claude-haiku-4-5` handled both
  single-word fixes and whole-answer rewrites in testing.
* To trade quality for cost, set `"local_fix": "first"` and raise `stop_min_hits`.

## Disclosures

* **Model calls.** Hermes: when an answer has leaks the local rules cannot fix,
  one extra call to your configured model rewrites those lines (usually 1–5
  seconds and a few hundred tokens). Oh My Pi / Pi: the same rewrite through the
  host's model library and credentials. OpenClaw: `api.runtime.llm` (falling
  back to an isolated run through the agent's runtime), and one more model pass
  for a leaking draft. Claude Code / Codex: a leaky answer triggers one short
  follow-up turn. Clean answers cost nothing extra.
* **Request rewriting (Hermes).** Progress notes of earlier steps that are in
  another language are replaced, in the request only, by a rewritten copy (one
  bounded model call for up to 8 notes, cached). The stored transcript is not
  changed.
* **Prompt additions.** A one-line rule is added to your message each turn, and
  in Hermes also to the end of each tool result (about 40–100 characters;
  `tool_reminder: false` turns the latter off).
* **Network.** None of its own. The only network use is the model call above,
  made by the host with the host's own credentials. The plugin never reads
  credential files.
* **Local files.** Claude Code / Codex keep a small per-session state file
  (target language, allowed languages, the words of your last message) in the
  plugin data directory, or `~/.pure-language/` when the host gives none; files
  idle for 7 days are deleted. Hermes, OpenClaw, Pi and Oh My Pi keep this in memory only. The optional
  log is described above.
* No telemetry, no background processes, no shell commands.

## Development

```bash
python -m unittest discover -s tests      # Python engine and hooks, no model calls
node --test tests/js/                     # JavaScript adapters against fake hosts
python tests/parity.py                    # both engines give the same results
python tools/export_tables.py             # after editing tables in engine/core.py
hermes plugins validate .
```

`engine/` is the reference engine; `__init__.py` is the Hermes adapter,
`hooks/run.py` the Claude Code / Codex adapter, `js/engine.mjs` the JavaScript
port, `js/pi-extension.mjs` the Pi / Oh My Pi extension and `js/openclaw.mjs`
the OpenClaw plugin. `PURE_LANGUAGE_DEBUG=1` makes the JavaScript adapters log
one line per hook call (no answer text).

## License

MIT, see [LICENSE](LICENSE). `engine/data/hanja_ko.json` and
`engine/data/ja_kanji.txt` are derived from the Unicode Character Database
(Unihan); see [NOTICE](NOTICE).
