// pure-language engine, JavaScript edition (ES module, no dependencies).
//
// A line-for-line port of engine/core.py so OpenClaw, Pi and Oh My Pi plugins can
// run the same rules in process. Word tables and texts come from
// engine/data/tables.json (exported from the Python engine by
// tools/export_tables.py); tests/parity.py checks that both engines agree.
//
// Public API
//   detectLanguage(text) -> code | null
//   resolveTurn(memo, sessionKey, message, def = "en") -> { target, allowed, skip }
//   requestedLanguages(message) -> Set
//   guardFor(target, allowed, full = false) -> string
//   clean(text, opts) -> Promise<{ text, hits }>   (opts.rewriter may be async)
//   cleanLocal(text, opts) -> { text, hits }       (no model; final marks)
//   leaks(text, opts) -> hits
//   wrongLanguage(text, target, allowed) -> code | null
//   userTokens(message) -> { userWords, userCjk }
//   rewriteInstructions / rewriteMaxTokens / numberLines / parseRewrite / REWRITE_SCHEMA

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const DATA = join(dirname(fileURLToPath(import.meta.url)), "..", "engine", "data");

function readJson(name, fallback) {
  try {
    return JSON.parse(readFileSync(join(DATA, name), "utf8"));
  } catch {
    return fallback;
  }
}

const T = readJson("tables.json", null);
if (!T) throw new Error("pure-language: engine/data/tables.json is missing");
const READ = readJson("hanja_ko.json", {});
let JA_HAN = new Set();
try {
  JA_HAN = new Set(readFileSync(join(DATA, "ja_kanji.txt"), "utf8"));
} catch {
  /* still works, just without the Japanese kanji table */
}

// ------------------------------------------------------------------ helpers
const set = (a) => new Set(a);
const g = (re) => new RegExp(re.source, re.flags.includes("g") ? re.flags : re.flags + "g");
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\\-#&~]/g, "\\$&").replace(/ /g, "\\ ");
const cpLen = (s) => [...s].length;
const isUpper = (s) => s === s.toUpperCase() && s !== s.toLowerCase();
const isLower = (s) => s === s.toLowerCase() && s !== s.toUpperCase();
const isAlpha = (c) => /\p{L}/u.test(c);
const findAll = (re, s) => [...s.matchAll(g(re))];
const WS = "\\s\\x1c-\\x1f\\x85";
const strip = (s) => s.replace(new RegExp(`^[${WS}]+|[${WS}]+$`, "g"), "");
const lstrip = (s) => s.replace(new RegExp(`^[${WS}]+`), "");
const rstrip = (s) => s.replace(new RegExp(`[${WS}]+$`), "");
const pySplitWs = (s) => strip(s).split(new RegExp(`[${WS}]+`)).filter(Boolean);
const head = (s, n) => [...s].slice(0, n).join("");

/** Python str.splitlines() */
export function splitlines(s) {
  if (!s) return [];
  const sep = /\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/;
  const out = s.split(sep);
  if (out.length > 1 && out[out.length - 1] === "") out.pop();
  return out;
}

const pyCmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
const sortedArr = (it) => [...it].sort(pyCmp);
const fmt = (tpl, vars) => tpl.replace(/\{(\w+)\}/g, (_, k) => vars[k]);
/** max(dict, key=dict.get): first key with the largest value, in insertion order */
function argmax(obj) {
  let best = null;
  for (const k of Object.keys(obj)) if (best === null || obj[k] > obj[best]) best = k;
  return best;
}

// ------------------------------------------------------------------ data
export const GUARD = T.GUARD;
const CJK_RE = /[\u3040-\u30FF\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]+/;
const KANA_RE = /[\u3040-\u30FF]/;
const ZH_FUNC = set(T.ZH_FUNC);
const PUNCT_MAP = T.PUNCT_MAP;
const WORDS = T.WORDS;
const WORD_RE = new RegExp(
  Object.keys(WORDS).sort((a, b) => cpLen(b) - cpLen(a)).map(escapeRe).join("|"),
  "g",
);
// a period ends the word only at a sentence end ("legally."), not in "file.txt"
const LATIN_RE = /(?<![A-Za-z0-9_./\\:@#%=&?+~-])([A-Za-z]{2,})(?![A-Za-z0-9_/\\:@#%=&?+~-]|\.[A-Za-z0-9_])/g;
const HANGUL_CH = /[\uAC00-\uD7A3]/;
const LATIN_ALLOW = set(T.LATIN_ALLOW);
const PROPER_COLLIDE = set(T.PROPER_COLLIDE);
const LATIN_WORDS = T.LATIN_WORDS;
const LATIN_VERB_STEMS = set(T.LATIN_VERB_STEMS);
const LATIN_COMMON = set(T.LATIN_COMMON);
const PARTICLE_RE = /^(으로|이랑|이|가|은|는|을|를|과|와|로|랑)(?=$|[\s.,!?;:)\]"'~…·*_])/;
const PAIR = T.PAIR;
const VERBAL_RE = /^(는데|는지|는다|는|고|면|지만|니까|다|서|도록|려|며|겠|었|았|자|게|기)/;

export const CODE_RE = new RegExp(
  "(^[ \\t]*(?:```|~~~)[^\\n]*\\n.*?^[ \\t]*(?:```|~~~)[ \\t]*$" +
    "|^[ \\t]*(?:```|~~~)[\\s\\S]*(?![\\s\\S])" +
    "|(?:^(?: {4}|\\t)[^\\n]*\\n?)+" +
    "|`[^`\\n]+`)",
  "gsm",
);

// Pasted terminal / log material
const TERM_PROMPT_RE = new RegExp(
  "^\\s*(?:PS\\s+[A-Za-z]:[\\\\/][^>\\n]*>" +
    "|[A-Za-z]:[\\\\/][^>\\n<|\"]*>(?!>)" +
    "|(?:\\d+:\\s*)?[a-z]{1,3}d>" +
    "|\\d+:\\d+>" +
    "|>>>?(?:\\s|$)|\\.\\.\\.\\s" +
    "|[\\w.-]+@[\\w.-]+(?::[^\\n$#]*)?[$#%](?:\\s|$)" +
    "|[$%]\\s+\\S)",
);
const TERM_MISC_RE = new RegExp(
  "\\x1b\\[|\\[\\d{1,3}(?:;\\d{1,3})*m|" +
    "^\\s*Traceback \\(most recent call last\\)|^\\s*File \"[^\"]+\", line \\d+|" +
    "^\\s*at [\\w.$<>]+\\(.*\\)\\s*$|^\\s*[\\w.]+(?:Error|Exception|Warning)(?::|$)|" +
    "^\\s*\\[?\\d{4}-\\d\\d-\\d\\d[ T]\\d\\d:\\d\\d|^\\s*\\d\\d:\\d\\d:\\d\\d[.,]\\d+|" +
    "^\\s*(?:ERR|ERROR|WARN|WARNING|INFO|DEBUG|FATAL)[\\]:\\s]|" +
    "^\\s*[+-]{3} [ab]/|^@@ ",
);

/** True for a pasted terminal, log or stack-trace line (never prose to fix). */
export function isTechnical(line) {
  if (!line || !strip(line)) return false;
  if (TERM_PROMPT_RE.test(line) || TERM_MISC_RE.test(line)) return true;
  const body = strip(line);
  if (/[\uAC00-\uD7A3\u3040-\u30FF\u4E00-\u9FFF]/.test(body) || cpLen(body) < 12) return false;
  const sym = findAll(/[\\/|<>{}[\]=$*;:_@#%^~`"'()0-9.-]/, body).length;
  return sym * 10 >= cpLen(body.replace(/ /g, "")) * 4;
}

const NON_LATIN_LETTER = /[\u0370-\u1FFF\u3040-\u30FF\u3400-\u9FFF\uAC00-\uD7A3]/;

/** Indexes of terminal / log lines; a prompt line starts an output block. */
export function technicalLines(lines) {
  const tech = new Set();
  let block = false;
  lines.forEach((line, i) => {
    if (TERM_PROMPT_RE.test(line || "")) {
      block = true;
      tech.add(i);
      return;
    }
    if (block && strip(line) && (NON_LATIN_LETTER.test(line) || rstrip(line).endsWith("?"))) block = false;
    if (block || isTechnical(line)) tech.add(i);
  });
  return tech;
}

export function stripTechnical(text) {
  const lines = splitlines(text || "");
  const tech = technicalLines(lines);
  return lines.filter((_, i) => !tech.has(i)).join("\n");
}

const HANGUL_RUN = /[\uAC00-\uD7A3\u1100-\u11FF\u3130-\u318F]+/;
const KANA_RUN = /[\u3040-\u30FF\u31F0-\u31FF]+/;
const HAN_RUN = /[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]+/;

// ------------------------------------------------------------ language table
const LANGS = T.LANGS;
export const LANG_NAMES = Object.fromEntries(Object.entries(LANGS).map(([k, v]) => [k, v[0]]));
const KO_NAMES = Object.fromEntries(Object.entries(LANGS).map(([k, v]) => [k, v[1]]));
const LANG_SCRIPT = Object.fromEntries(Object.entries(LANGS).map(([k, v]) => [k, v[3]]));
const STOP = Object.fromEntries(
  Object.entries(LANGS).filter(([, v]) => v[4]).map(([k, v]) => [k, set(pySplitWs(v[4]))]),
);
const ALIASES = Object.fromEntries(Object.entries(LANGS).map(([k, v]) => [k, [v[0], v[1], ...v[2]]]));
const SCRIPT_RANGES = T.SCRIPT_RANGES;
const SCRIPT_RE = Object.fromEntries(Object.entries(SCRIPT_RANGES).map(([s, r]) => [s, new RegExp(`[${r}]+`, "g")]));
const SCRIPT_DEFAULT = T.SCRIPT_DEFAULT;
const EN_DISTINCT = set(T.EN_DISTINCT);
const EN_COLLIDE = Object.fromEntries(Object.entries(T.EN_COLLIDE).map(([k, v]) => [k, set(v)]));

const ALIAS_TO_LANG = {};
for (const [l, al] of Object.entries(ALIASES)) for (const a of al) ALIAS_TO_LANG[a.toLowerCase()] = l;
const ALIAS_ALT = Object.keys(ALIAS_TO_LANG).sort((a, b) => cpLen(b) - cpLen(a)).map(escapeRe).join("|");

const FULL_CMD = [
  new RegExp(`(?<l>${ALIAS_ALT})\\s*(?:으로|로)\\s*(?:만\\s*)?(?:답|대답|답변|말|설명|대화|얘기|이야기|응답)`, "gi"),
  new RegExp(`(?:answer|respond|reply|speak|talk|explain|write (?:your|the) (?:answer|reply|response)s?)\\s+(?:only\\s+)?in\\s+(?<l>${ALIAS_ALT})`, "gi"),
  new RegExp(`(?:use|switch to)\\s+(?<l>${ALIAS_ALT})(?:\\s+(?:only|from now on|please))?\\s*[.!]?$`, "gim"),
  new RegExp(`(?<l>${ALIAS_ALT})\\s*で\\s*(?:答え|回答|返答|返事|説明|話|会話)`, "gi"),
  new RegExp(`(?:用|以|请用|請用)\\s*(?<l>${ALIAS_ALT})\\s*(?:回答|回复|回覆|答复|说明|說明|交流|对话|說|说)`, "gi"),
];
const PART_CMD = [
  new RegExp(`(?<l>${ALIAS_ALT})\\s*(?:으로|로)?\\s*[^\\n]{0,15}?(?:작성|써|쓰|번역|초안|만들|보내|준비|표현|예시|문구|메일|원문|그대로)`, "gi"),
  new RegExp(`(?:translate|draft|write|compose|phrase)[^\\n]{0,40}?(?:in|into)\\s+(?<l>${ALIAS_ALT})`, "gi"),
  new RegExp(`(?<l>${ALIAS_ALT})\\s*(?:に翻訳|で書いて|の文面|に訳)`, "gi"),
  new RegExp(`(?:翻译成|翻譯成|译成|写成)\\s*(?<l>${ALIAS_ALT})`, "gi"),
];
const ALLOW_ALL_RE = /외국어 허용|allow foreign/i;
const URL_OR_PATH = /https?:\/\/\S+|\S+[\\/]\S+/g;
const VI_CHARS = /[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]/gi;

function stripNoise(msg) {
  msg = stripTechnical(msg.replace(CODE_RE, " "));
  msg = msg.replace(URL_OR_PATH, " ");
  return splitlines(msg).filter((l) => !lstrip(l).startsWith(">")).join("\n");
}

const runLen = (re, text) => findAll(re, text).reduce((n, m) => n + m[0].length, 0);

function scriptCounts(text) {
  const c = {
    ko: findAll(/[\uAC00-\uD7A3]/, text).length,
    ja: findAll(/[\u3040-\u30FF]/, text).length,
    han: runLen(HAN_RUN, text),
    latin: findAll(/[A-Za-zÀ-ÿĀ-ž]/, text).length,
    vi: findAll(VI_CHARS, text).length,
  };
  for (const [s, rx] of Object.entries(SCRIPT_RE)) c[s] = runLen(rx, text);
  return c;
}

function latinLang(text) {
  const words = findAll(/[a-zà-ÿā-ž']+/, text.toLowerCase()).map((m) => m[0]);
  if (!words.length) return "en";
  const ws = set(words);
  const scores = {};
  for (const [l, st] of Object.entries(STOP)) {
    if (LANG_SCRIPT[l] !== "latin") continue;
    let n = 0;
    for (const w of ws) if (st.has(w)) n++;
    scores[l] = n;
  }
  if (findAll(VI_CHARS, text).length >= 2) scores.vi = (scores.vi || 0) + 3;
  const best = argmax(scores);
  return scores[best] ? best : "en";
}

function cyrillicLang(text) {
  const t = text.toLowerCase();
  if (/[іїєґ]/.test(t)) return "uk";
  if (/[әғқңөұүһі]/.test(t)) return "kk";
  if (/[өү]/.test(t)) return "mn";
  if (/[ђћџљњј]/.test(t)) return "sr";
  const ws = set(findAll(/[\u0400-\u04FF]+/, t).map((m) => m[0]));
  const scores = {};
  for (const l of ["ru", "bg", "uk", "sr"]) {
    let n = 0;
    for (const w of ws) if (STOP[l].has(w)) n++;
    scores[l] = n;
  }
  const best = argmax(scores);
  return scores[best] ? best : "ru";
}

function arabicLang(text) {
  if (/[ٹڈڑںےۓ]/.test(text)) return "ur";
  if (/[پچژگکی]/.test(text)) return "fa";
  return "ar";
}

/** Best guess of the language a message is written in, or null if too short. */
export function detectLanguage(text) {
  const t = stripNoise(text || "");
  const c = scriptCounts(t);
  const others = Object.fromEntries(Object.keys(SCRIPT_RANGES).map((s) => [s, c[s]]));
  const top = argmax(others);
  if (c.ja >= 2 && c.ja * 3 >= c.han) return "ja";
  if (c.ko >= 2 && c.ko >= c.han && c.ko >= others[top]) return "ko";
  if (c.han >= 2 && c.han >= others[top]) return c.ja ? "ja" : "zh";
  if (others[top] >= 3 && others[top] * 2 >= Math.floor(c.latin / 3)) {
    if (top === "cyrillic") return cyrillicLang(t);
    if (top === "arabic") return arabicLang(t);
    if (top === "devanagari") {
      if (/(?<![\u0900-\u097F\w])(आहे|आणि|नाही)(?![\u0900-\u097F\w])/.test(t)) return "mr";
      if (/(छ|हुन्छ|गर्नु)/.test(t) && !/है/.test(t)) return "ne";
      return "hi";
    }
    return SCRIPT_DEFAULT[top];
  }
  if (c.latin >= 8) return latinLang(t);
  return null;
}

function instructionLanguage(msg) {
  const raw = splitlines(msg || "").filter((l) => strip(l));
  const tech = technicalLines(raw);
  const prose = raw.map((_, i) => i).filter((i) => !tech.has(i) && !lstrip(raw[i]).startsWith(">"));
  for (const idx of prose.slice(-3).reverse()) {
    const lang = detectLanguage(raw[idx]);
    if (lang) return [lang, [...raw.slice(0, idx), ...raw.slice(idx + 1)].join("\n")];
  }
  return [detectLanguage(msg), ""];
}

// "왜 영어로 대답했지?", "why did you answer in English?": talk about a past reply, not a request
const PAST_AFTER_RE = /^\s*(?:했|하였|한\s|한거|한 거|된|되었|하지\s*않|안\s*했)/;
// Korean requests: "영어로 대답해", "영어로 답해 줘", "영어로 말하라고", "영어로 답변 부탁해", "영어로만."
const KO_REQUEST_AFTER_RE = new RegExp(
  "^(?:변|하)?\\s*(?:을|를|은|는|도|만|좀)?\\s*" +
  "(?:해|하세요|하십시오|하시오|하라|해라|하자|합시다|할\\s*것|하도록|해야|하면|하기\\s*바|하길|" +
  "줘|주세요|주십시오|주시|주라|부탁|바랍|바래|요청|[.!]?\\s*$)");
const ASK_WHY_RE = /\b(?:why|did you|didn't you|have you|were you)\b|왜|어째서|なぜ|どうして|为什么|為什麼/i;

function findCmd(patterns, msg, commandsOnly = false) {
  const found = [];
  msg = msg || "";
  for (const pat of patterns) {
    for (const m of msg.matchAll(pat)) {
      const lang = ALIAS_TO_LANG[m.groups.l.toLowerCase()];
      if (!lang) continue;
      if (commandsOnly) {
        const end = m.index + m[0].length;
        const a = msg.lastIndexOf("\n", m.index - 1) + 1;
        const b = msg.indexOf("\n", end);
        const line = msg.slice(a, b >= 0 ? b : msg.length);
        if (PAST_AFTER_RE.test(msg.slice(end)) || ASK_WHY_RE.test(line)) continue;
        // "영어로 대답하고", "영어로 답하는": a description, not a request
        if (pat === patterns[0] && !KO_REQUEST_AFTER_RE.test(msg.slice(end))) continue;
      }
      found.push([m.index, lang]);
    }
  }
  found.sort((a, b) => a[0] - b[0] || pyCmp(a[1], b[1]));
  return found.map((f) => f[1]);
}

const GUARD_PREFIXES = [...new Set([
  ...Object.values(T.GUARDS), ...Object.values(T.GUARDS_SHORT), T.GUARD_GENERIC, T.GUARD_SHORT_GENERIC,
].filter((t) => t.slice(0, 40).includes("]")).map((t) => t.slice(0, t.indexOf("]") + 1)).concat(["[pure-language]"]))];
// notices the host appends to the user's message ("[System: The skills ... changed ...]")
const HOST_NOTE_RE = /^[ \t]*\[(?:System|SYSTEM|IMPORTANT|Note|NOTE|Context|CONTEXT)\b[\s\S]*?\][ \t]*$/gm;
// the whole message is the host's (background job finished, nudges)
const HOST_TURN_RE = /^\s*\[(?:System|SYSTEM|IMPORTANT)\s*:/;
// one-line wrapper tags ("[OUT-OF-BAND USER MESSAGE — ...]", "[/OUT-OF-BAND USER MESSAGE]")
const HOST_TAG_RE = /^[ \t]*\[\/?[A-Z][A-Z0-9_-]+(?:[ _-][A-Z0-9_-]+)+\b[^\]\n]*\][ \t]*$/gm;

/** The user's own words: drop host notices and this plugin's rule lines. */
export function stripHostNotes(msg) {
  if (HOST_TURN_RE.test(msg || "")) return "";
  msg = (msg || "").replace(HOST_NOTE_RE, "").replace(HOST_TAG_RE, "");
  return strip(splitlines(msg).filter((l) => !GUARD_PREFIXES.some((p) => lstrip(l).startsWith(p))).join("\n"));
}

/** Languages the user explicitly asked to appear (only these are announced). */
export function requestedLanguages(msg) {
  return set(findCmd(PART_CMD, msg || ""));
}

/** The rule text injected before the answer; ``full`` after a leak. */
export function guardFor(target, allowed, full = false) {
  const name = LANG_NAMES[target] ?? target;
  let base = full
    ? T.GUARDS[target] || fmt(T.GUARD_GENERIC, { name })
    : T.GUARDS_SHORT[target] || fmt(T.GUARD_SHORT_GENERIC, { name });
  const extra = sortedArr([...allowed].filter((l) => l !== target));
  if (extra.length) {
    const names = extra.map((l) => (target === "ko" ? KO_NAMES[l] ?? l : LANG_NAMES[l] ?? l)).join(", ");
    const notes = full ? T.ALLOW_NOTE : T.ALLOW_SHORT;
    base += fmt(notes[target] ?? (full ? T.ALLOW_NOTE.en : " Allowed: {names}."), { names });
  }
  return base;
}

// ------------------------------------------------- non-Korean target scanning
const spansOf = (re, line) => findAll(re, line).map((m) => [m.index, m.index + m[0].length]);

function scanForeign(line, target, allowed, exempt, userText, res, final, scriptsOnly = false) {
  const ok = set([...allowed].map((l) => LANG_SCRIPT[l] ?? "latin"));
  ok.add(LANG_SCRIPT[target] ?? "latin");
  let spans = [];
  if (!scriptsOnly) {
    if (!ok.has("hangul")) spans.push(...spansOf(HANGUL_RUN, line));
    if (!ok.has("ja")) spans.push(...spansOf(KANA_RUN, line));
    if (!ok.has("ja") && !ok.has("han")) spans.push(...spansOf(HAN_RUN, line));
    else if (ok.has("ja") && !ok.has("han") && JA_HAN.size) {
      for (const m of findAll(HAN_RUN, line)) {
        for (let k = 0; k < m[0].length; k++) {
          if (!JA_HAN.has(m[0][k])) spans.push([m.index + k, m.index + k + 1]);
        }
      }
    }
  }
  for (const [s, rx] of Object.entries(SCRIPT_RE)) if (!ok.has(s)) spans.push(...spansOf(rx, line));
  if (!scriptsOnly) {
    if (!ok.has("latin")) {
      for (const m of line.matchAll(LATIN_RE)) {
        const w = m[1];
        const low = w.toLowerCase();
        if (isUpper(w) || isUpper(w[0]) || LATIN_ALLOW.has(low) || exempt.has(low)) continue;
        if (LATIN_COMMON.has(low)) spans.push([m.index, m.index + m[0].length]);
      }
    } else if (target !== "en" && !allowed.has("en") && LANG_SCRIPT[target] === "latin") {
      const stop = STOP[target] || new Set();
      const coll = EN_COLLIDE[target] || new Set();
      for (const m of line.matchAll(LATIN_RE)) {
        const w = m[1];
        const low = w.toLowerCase();
        if (EN_DISTINCT.has(low) && !stop.has(low) && !coll.has(low) && !exempt.has(low) &&
            (isLower(w) || isLower(w.slice(1)))) spans.push([m.index, m.index + m[0].length]);
      }
    }
  }
  spans = spans.filter(([a, b]) => !userText.includes(line.slice(a, b)));
  if (!spans.length) return line;
  spans.sort((x, y) => x[0] - y[0] || x[1] - y[1]);
  for (const [a, b] of spans) {
    res.hits.push([`${target}-${final ? "mark" : "rewrite"}`, line.slice(a, b), line.slice(Math.max(0, a - 20), b + 20)]);
  }
  if (!final) {
    res.unresolved = true;
    return line;
  }
  const out = [];
  let pos = 0;
  for (const [a, b] of spans) {
    if (a < pos) continue;
    out.push(line.slice(pos, b), MK);
    pos = b;
  }
  out.push(line.slice(pos));
  return out.join("");
}

// ------------------------------------------------------------- Korean helpers
const isSyl = (ch) => !!ch && ch >= "\uAC00" && ch <= "\uD7A3";
const jong = (ch) => (isSyl(ch) ? (ch.charCodeAt(0) - 0xac00) % 28 : 0);
const compose = (cho, jung, jg) => String.fromCharCode(0xac00 + (cho * 21 + jung) * 28 + jg);
function splitSyl(ch) {
  const i = ch.charCodeAt(0) - 0xac00;
  return [Math.floor(i / 588), Math.floor((i % 588) / 28), i % 28];
}
const Y_VOWELS = new Set([2, 6, 7, 12, 17, 20]);

function initialRule(syl) {
  if (!isSyl(syl)) return syl;
  const [cho, jung, jg] = splitSyl(syl);
  if (cho === 5) return compose(Y_VOWELS.has(jung) ? 11 : 2, jung, jg);
  if (cho === 2 && Y_VOWELS.has(jung)) return compose(11, jung, jg);
  return syl;
}

function medialRule(prev, syl) {
  if ((syl === "렬" || syl === "률") && prev && [0, 4].includes(jong(prev))) return syl === "렬" ? "열" : "율";
  return syl;
}

function readHanja(run) {
  const res = [];
  const chars = [...run];
  for (let i = 0; i < chars.length; i++) {
    let r = READ[chars[i]];
    if (!r) return null;
    r = i === 0 ? initialRule(r) : medialRule(res[res.length - 1], r);
    res.push(r);
  }
  return res.join("");
}

function fixParticle(repl, rest) {
  const m = PARTICLE_RE.exec(rest);
  if (!m || !repl) return [rest, 0];
  const cur = m[1];
  const [withB, withoutB] = PAIR[cur];
  const j = jong(repl[repl.length - 1]);
  let want = j ? withB : withoutB;
  if ((cur === "으로" || cur === "로") && j === 8) want = "로";
  return [want, cur.length];
}

// ------------------------------------------------------------------ cleaning
const newLine = () => ({ text: "", hits: [], unresolved: false });
const hasZh = (s) => [...s].some((c) => ZH_FUNC.has(c));

function cleanCjk(seg, userCjk, res, final) {
  for (const [src, dst] of Object.entries(PUNCT_MAP)) if (seg.includes(src)) seg = seg.split(src).join(dst);
  if (!CJK_RE.test(seg)) return seg;
  const out = [];
  let pos = 0;
  for (const m of findAll(CJK_RE, seg)) {
    const start = m.index;
    if (start < pos) continue;
    const run = m[0];
    out.push(seg.slice(pos, start));
    pos = start + run.length;
    if (userCjk.has(run)) {
      out.push(run);
      continue;
    }
    const ctx = seg.slice(Math.max(0, start - 20), pos + 20);
    const parts = [];
    let last = 0;
    for (const w of run.matchAll(WORD_RE)) {
      res.hits.push(["cjk-word", w[0], ctx]);
      parts.push([run.slice(last, w.index), false]);
      parts.push([WORDS[w[0]], true]);
      last = w.index + w[0].length;
    }
    parts.push([run.slice(last), false]);
    let text = "";
    for (const [piece, done] of parts) {
      if (done || !piece) {
        text += piece;
        continue;
      }
      const needsRewrite = KANA_RE.test(piece) || hasZh(piece) || readHanja(piece) === null;
      if (needsRewrite && !final) {
        res.unresolved = true;
        res.hits.push(["cjk-rewrite", piece, ctx]);
        text += piece;
        continue;
      }
      const reading = readHanja(piece);
      if (reading && !KANA_RE.test(piece) && !hasZh(piece)) {
        res.hits.push(["cjk-read", piece, ctx]);
        text += reading;
      } else {
        res.hits.push(["cjk-mark", piece, ctx]);
        text += piece + MK;
      }
    }
    if (text && text[0] !== MK && isSyl(text[0])) {
      const prev = start ? seg[start - 1] : "";
      if (!HANGUL_CH.test(prev)) text = initialRule(text[0]) + text.slice(1);
    }
    out.push(text);
    const [ptxt, used] = fixParticle(text, seg.slice(pos));
    if (used) {
      out.push(ptxt);
      pos += used;
    }
  }
  out.push(seg.slice(pos));
  return out.join("");
}

const PHRASE_RE = /(?<![A-Za-z0-9_./\\:@#%=&?+~`-])[a-z]{2,}(?:[ -][a-z]{2,})+(?![A-Za-z0-9_/\\:@#%=&?+~`-]|\.[A-Za-z0-9_])/g;
const PHRASE_ALLOW = set(T.PHRASE_ALLOW);

function englishPhrases(seg, exempt) {
  const found = [];
  for (const m of seg.matchAll(PHRASE_RE)) {
    const words = m[0].split(/[ -]/);
    if (LATIN_ALLOW.has(words[0]) || words.every((w) => exempt.has(w))) continue;
    if (PHRASE_ALLOW.has(m[0])) continue;
    if (!HANGUL_CH.test(seg.slice(Math.max(0, m.index - 30), m.index + m[0].length + 30))) continue;
    found.push(m);
  }
  return found;
}

const PROSE_SUFFIX = /(?:ly|tion|sion|ment|ness|ive|ful|ous|able|ible|ity|ance|ence)$/;

function cleanLatin(seg, exempt, res, final, gluedOnly = false) {
  const phrases = gluedOnly ? [] : englishPhrases(seg, exempt);
  if (phrases.length) {
    for (const m of phrases) {
      res.hits.push([`en-phrase-${final ? "mark" : "rewrite"}`, m[0], seg.slice(Math.max(0, m.index - 20), m.index + m[0].length + 20)]);
    }
    if (!final) res.unresolved = true;
    else {
      const out = [];
      let pos = 0;
      for (const m of phrases) {
        const end = m.index + m[0].length;
        out.push(seg.slice(pos, end), MK);
        pos = end;
      }
      out.push(seg.slice(pos));
      seg = out.join("");
    }
  }
  const out = [];
  let pos = 0;
  for (const m of seg.matchAll(LATIN_RE)) {
    const start = m.index;
    const end = start + m[0].length;
    if (start < pos) continue;
    const word = m[1];
    const low = word.toLowerCase();
    if (isUpper(word)) continue;
    if (isUpper(word[0])) {
      if (!isLower(word.slice(1)) || PROPER_COLLIDE.has(low) || !LATIN_COMMON.has(low)) continue;
    }
    if (LATIN_ALLOW.has(low) || exempt.has(low)) continue;
    const before = start ? seg[start - 1] : "";
    const after = end < seg.length ? seg[end] : "";
    const glued = HANGUL_CH.test(before) || HANGUL_CH.test(after);
    if (gluedOnly && !glued) continue;
    if (!glued && !LATIN_COMMON.has(low)) {
      if (!(isLower(word) && word.length >= 5 && PROSE_SUFFIX.test(word) &&
            (HANGUL_CH.test(seg.slice(Math.max(0, start - 12), start)) || HANGUL_CH.test(seg.slice(end, end + 12))))) continue;
    }
    if (!HANGUL_CH.test(seg.slice(Math.max(0, start - 40), end + 40))) continue;
    const ctx = seg.slice(Math.max(0, start - 20), end + 20);
    out.push(seg.slice(pos, start));
    pos = end;
    if (Object.prototype.hasOwnProperty.call(LATIN_WORDS, low)) {
      const repl = LATIN_WORDS[low];
      res.hits.push(["en-fixed", word, ctx]);
      const rest = seg.slice(pos);
      const [ptxt, used] = fixParticle(repl, rest);
      if (used) {
        out.push(repl + ptxt);
        pos += used;
      } else if (!LATIN_VERB_STEMS.has(low) && VERBAL_RE.test(rest)) out.push(repl + "하");
      else out.push(repl);
    } else if (!final) {
      res.unresolved = true;
      res.hits.push(["en-rewrite", word, ctx]);
      out.push(word);
    } else {
      res.hits.push(["en-mark", word, ctx]);
      out.push(word + MK);
    }
  }
  out.push(seg.slice(pos));
  return out.join("");
}

const TARGET_CHARS = T.TARGET_CHARS;
const TECH_CH = /[\\/:._=<>|*[\]{}()'"$#@-]/;
const TRIM_CH = " \t,.;!?()\"'“”‘’「」";

function targetChars(target) {
  const script = LANG_SCRIPT[target] ?? "latin";
  return TARGET_CHARS[script] ?? SCRIPT_RANGES[script] ?? "";
}

function verbatimSpans(line, target, context) {
  if (!context) return [];
  const chars = targetChars(target);
  if (!chars) return [];
  const spans = [];
  for (const m of line.matchAll(new RegExp(`[^${chars}\\n]+`, "g"))) {
    let a = m.index;
    let b = a + m[0].length;
    while (a < b && TRIM_CH.includes(line[a])) a++;
    while (b > a && TRIM_CH.includes(line[b - 1])) b--;
    const piece = line.slice(a, b);
    if (!piece || !context.includes(piece)) continue;
    const cjk = CJK_RE.test(piece) || HANGUL_RUN.test(piece);
    if (pySplitWs(piece).length >= 3 || cpLen(piece) >= 16 || (cjk && cpLen(piece) >= 10) ||
        (TECH_CH.test(piece) && cpLen(piece) >= 6)) spans.push([a, b]);
  }
  return spans;
}

function cleanLine(line, exempt, userCjk, final, kw) {
  const spans = verbatimSpans(line, kw.target, kw.context);
  if (!spans.length) return cleanLineRaw(line, exempt, userCjk, final, kw);
  const keep = [];
  const maskedParts = [];
  let pos = 0;
  spans.forEach(([a, b], k) => {
    maskedParts.push(line.slice(pos, a), String.fromCharCode(0xe000 + k));
    keep.push(line.slice(a, b));
    pos = b;
  });
  maskedParts.push(line.slice(pos));
  const res = cleanLineRaw(maskedParts.join(""), exempt, userCjk, final, kw);
  keep.forEach((piece, k) => {
    res.text = res.text.split(String.fromCharCode(0xe000 + k)).join(piece);
  });
  return res;
}

const GLOSS_RE = /([\u3040-\u30FF\u3400-\u9FFF\uF900-\uFAFF]+)\(([\uAC00-\uD7A3][\uAC00-\uD7A3 ·]*)\)/g;

const fixGloss = (line, quoteOk) => line.replace(GLOSS_RE, (_, orig, gloss) => (quoteOk ? `${gloss}(\`${orig}\`)` : gloss));

function glued(text, start, end) {
  const before = start ? text[start - 1] : "";
  const after = end < text.length ? text[end] : "";
  return HANGUL_CH.test(before) || HANGUL_CH.test(after);
}

function masked(text, rx, pick, fn) {
  const keep = [];
  const hidden = text.replace(g(rx), (m0, offset) => {
    if (pick(m0, offset)) return m0;
    keep.push(m0);
    return String.fromCharCode(0xf000 + keep.length - 1);
  });
  let out = fn(hidden);
  keep.forEach((piece, k) => {
    out = out.split(String.fromCharCode(0xf000 + k)).join(piece);
  });
  return out;
}

function cleanLineRaw(line, exempt, userCjk, final, kw) {
  const { target, allowed, userText } = kw;
  const res = newLine();
  if (target !== "ko") {
    res.text = scanForeign(line, target, allowed, exempt, userText, res, final);
    return res;
  }
  const cjkOk = allowed.has("ja") || allowed.has("zh");
  let t = fixGloss(line, cjkOk);
  if (!cjkOk) t = cleanCjk(t, userCjk, res, final);
  else {
    const base = t;
    t = masked(base, CJK_RE, (m0, off) => glued(base, off, off + m0.length) || (!allowed.has("zh") && hasZh(m0)),
      (x) => cleanCjk(x, userCjk, res, final));
  }
  t = cleanLatin(t, exempt, res, final, allowed.has("en"));
  if (!res.unresolved || final) t = scanForeign(t, target, allowed, exempt, userText, res, final, true);
  else scanForeign(t, target, allowed, exempt, userText, res, final, true);
  res.text = t;
  return res;
}

export function rewriteInstructions(target, allowed, sample) {
  const name = LANG_NAMES[target] ?? target;
  let text = `Each line mixes in words from other languages. Rewrite each line entirely in natural ${name}, translating every foreign word; same meaning. `;
  if (sample && !(target in LANG_NAMES)) text += `Target language = language of: ${head(sample, 120)}. `;
  const keepL = sortedArr([...allowed].filter((l) => l !== target));
  if (keepL.length) text += `Keep ${keepL.map((l) => LANG_NAMES[l] ?? l).join(", ")} text as is. `;
  return text + "Keep markdown, numbers, `code`, URLs, paths, names, ACRONYMS. Same line count and order, no numbering.";
}

export const rewriteMaxTokens = (lines) => 64 + lines.reduce((n, l) => n + cpLen(l), 0) * 2;

export const REWRITE_SCHEMA = {
  type: "object", additionalProperties: false, required: ["lines"],
  properties: { lines: { type: "array", items: { type: "string" } } },
};

export const numberLines = (lines) => lines.map((l, i) => `${i + 1}\t${l}`).join("\n");

export function parseRewrite(parsed, n) {
  if (!parsed || typeof parsed !== "object" || !Array.isArray(parsed.lines)) return null;
  const got = parsed.lines.map((x) => String(x).replace(/^\d+\t/, ""));
  return got.length === n ? got : null;
}

const INLINE_KEEP = /`[^`\n]+`|https?:\/\/\S+|\[[^\]]*\]\([^)]*\)/g;
const PREFIX_RE = /^\s*(?:[-*+]|\d+[.)]|#{1,6}|>|\|)?\s*/;

function structureOk(orig, neu) {
  if (typeof neu !== "string" || !strip(neu)) return false;
  if (strip(neu).includes("\n")) return false;
  if (strip(PREFIX_RE.exec(orig)[0]) !== strip(PREFIX_RE.exec(neu)[0])) return false;
  if (orig.split("|").length !== neu.split("|").length) return false;
  if (findAll(INLINE_KEEP, orig).some((m) => !neu.includes(m[0]))) return false;
  return cpLen(neu) <= Math.max(40, cpLen(orig) * 3);
}

const FENCE = /^[ \t]*(```|~~~)/;
const INLINE_CODE = /`[^`\n]+`/g;

function proseIndexes(lines) {
  const out = [];
  let fenced = false;
  const tech = technicalLines(lines);
  lines.forEach((line, i) => {
    if (FENCE.test(line)) {
      fenced = !fenced;
      return;
    }
    if (!fenced && !tech.has(i)) out.push(i);
  });
  return out;
}

const letters = (text) => [...text].filter(isAlpha);

/** The language a whole answer is written in when it is not the target, else null. */
export function wrongLanguage(text, target, allowed = new Set(), minLetters = 40) {
  allowed = new Set(allowed);
  const lines = (text || "").split("\n");
  let prose = proseIndexes(lines).map((i) => lines[i].replace(INLINE_CODE, " ")).join("\n");
  prose = prose.replace(URL_OR_PATH, " ");
  const lets = letters(prose);
  if (lets.length < minLetters) return null;
  const lang = detectLanguage(prose);
  if (!lang || lang === target || allowed.has(lang)) return null;
  const chars = targetChars(target);
  if (!chars) return null;
  const own = findAll(new RegExp(`[${chars}]`), prose).length;
  if (own * 100 >= lets.length * 15) return null;
  return lang;
}

function wholeLines(lines, target) {
  const own = new RegExp(`[${targetChars(target)}]`);
  return proseIndexes(lines).filter((i) => letters(lines[i].replace(INLINE_CODE, " ")).filter((c) => !own.test(c)).length >= 3);
}

async function wholeRewrite(text, target, allowed, userText, rewriter, lang, hits, limit) {
  const lines = text.split("\n");
  const own = new RegExp(`[${targetChars(target)}]`);
  let idxs = wholeLines(lines, target);
  if (!idxs.length) return text;
  hits.push([`whole-${rewriter ? "rewrite" : "mark"}`, lang, head(strip(lines[idxs[0]]), 60)]);
  if (!rewriter) return text;
  idxs = idxs.slice(0, limit);
  let got;
  try {
    got = (await rewriter(idxs.map((i) => lines[i]), target, allowed, userText)) || [];
  } catch (exc) {
    console.warn(`pure-language: whole-answer rewrite failed: ${exc}`);
    return text;
  }
  idxs.forEach((i, k) => {
    const gl = got[k];
    if (gl && strip(gl) && structureOk(lines[i], gl) && own.test(gl)) {
      hits.push(["rewritten", head(strip(lines[i]), 60), head(strip(gl), 60)]);
      lines[i] = gl;
    }
  });
  return lines.join("\n");
}

function sentences(line) {
  const out = [];
  let start = 0;
  let ticks = 0;
  for (let k = 0; k < line.length; k++) {
    const ch = line[k];
    if (ch === "`") ticks++;
    else if (".!?。！？".includes(ch) && ticks % 2 === 0 && k + 1 < line.length && line[k + 1] === " ") {
      let end = k + 1;
      while (end < line.length && line[end] === " ") end++;
      out.push([start, k + 1]);
      start = end;
    }
  }
  if (start < line.length) out.push([start, line.length]);
  return out;
}

const REWRITE_CACHE = new Map();
const CACHE_MAX = 500;

function prepare(opts) {
  const target = opts.target ?? "ko";
  const allowedIn = opts.allowed ? [...opts.allowed] : [];
  const allowed = new Set(allowedIn.length ? allowedIn : [target]);
  const userText = opts.userText ?? "";
  return {
    target, allowed, userText,
    userWords: new Set(opts.userWords ?? []),
    userCjk: new Set(opts.userCjk ?? []),
    context: userText + "\n" + (opts.context ?? ""),
    maxLines: Math.max(1, Math.trunc(opts.maxLines ?? 20)),
    localFirst: Boolean(opts.localFirst),
  };
}

function firstPass(text, o, final, needs = (r) => r.unresolved) {
  const kw = { target: o.target, allowed: o.allowed, userText: o.userText, context: o.context };
  const parts = text.split(CODE_RE);
  const codeTerms = new Set();
  for (let i = 1; i < parts.length; i += 2) for (const m of findAll(/[A-Za-z]{2,}/, parts[i] ?? "")) codeTerms.add(m[0].toLowerCase());
  const exempt = new Set([...o.userWords, ...codeTerms]);
  const hits = [];
  const pending = [];
  const splitParts = new Map();
  for (let i = 0; i < parts.length; i += 2) {
    const lines = parts[i].split("\n");
    const tech = technicalLines(lines);
    lines.forEach((line, j) => {
      if (tech.has(j)) return;
      const r = cleanLine(line, exempt, o.userCjk, final, kw);
      hits.push(...r.hits);
      if (needs(r)) pending.push([i, j, line]);
      lines[j] = r.text;
    });
    splitParts.set(i, lines);
  }
  return { kw, parts, exempt, hits, pending, splitParts };
}

const join_ = (st) => {
  for (const [i, lines] of st.splitParts) st.parts[i] = lines.join("\n");
  return st.parts.join("");
};

/** Full cleaning. ``opts.rewriter(lines, target, allowed, sample)`` may be async. */
// private-use placeholder put after a leak that stays unfixed; turned into ⚠ or dropped
const MK = "\uE0A0";
const unmark = (text, mark) => text.split(MK).join(mark ? "⚠" : "");

export async function clean(text, opts = {}) {
  const rewrite = opts.rewrite ?? true;
  const rewriter = rewrite ? opts.rewriter ?? null : null;
  const o = prepare(opts);
  const lang = wrongLanguage(text, o.target, o.allowed);
  if (lang) {
    const hits = [];
    const out = await wholeRewrite(text, o.target, o.allowed, o.userText, rewriter, lang, hits, Math.max(60, o.maxLines));
    return { text: out, hits };
  }
  // With a rewriter every leaking line goes to the model; word tables and Hanja readings
  // can read awkwardly, so they are only the fallback (opts.localFirst restores rules first).
  const modelAll = Boolean(rewrite && rewriter && !o.localFirst);
  const needs = modelAll ? (r) => r.unresolved || r.hits.length > 0 : (r) => r.unresolved;
  const st = firstPass(text, o, !rewrite, needs);
  const { kw, exempt, hits, pending, splitParts } = st;
  if (pending.length) {
    const allowedKey = sortedArr(o.allowed).join(",");
    const ck = (u) => `${o.target}\u0000${allowedKey}\u0000${u}`;
    const plans = pending.map(([, , orig]) => {
      const units = [];
      if (cpLen(orig) > 80) {
        for (const [a, b] of sentences(orig)) {
          if (needs(cleanLine(orig.slice(a, b), exempt, o.userCjk, false, kw))) units.push([a, b]);
        }
      }
      return units.length ? units : [[0, orig.length]];
    });
    let todo = [];
    pending.forEach(([, , orig], k) => {
      for (const [a, b] of plans[k]) todo.push(orig.slice(a, b));
    });
    todo = [...new Set(todo)].filter((u) => !REWRITE_CACHE.has(ck(u))).slice(0, o.maxLines);
    if (todo.length && rewrite && rewriter) {
      let got = [];
      try {
        got = (await rewriter(todo, o.target, o.allowed, o.userText)) || [];
      } catch (exc) {
        console.warn(`pure-language: rewriter failed: ${exc}`);
      }
      todo.forEach((u, k) => {
        if (k < got.length && structureOk(u, got[k])) {
          if (REWRITE_CACHE.size >= CACHE_MAX) REWRITE_CACHE.delete(REWRITE_CACHE.keys().next().value);
          REWRITE_CACHE.set(ck(u), got[k]);
        }
      });
    }
    pending.forEach(([i, j, orig], k) => {
      const cand = [];
      let pos = 0;
      let used = 0;
      for (const [a, b] of plans[k]) {
        let gl = REWRITE_CACHE.get(ck(orig.slice(a, b)));
        cand.push(orig.slice(pos, a));
        gl = gl && strip(gl) ? (a === 0 && b === orig.length ? gl : strip(gl)) : null;
        if (gl !== null && !cleanLine(gl, exempt, o.userCjk, true, kw).hits.some((h) => h[0].endsWith("mark"))) {
          cand.push(gl);
          used++;
        } else cand.push(orig.slice(a, b));
        pos = b;
      }
      cand.push(orig.slice(pos));
      const fixed = cleanLine(cand.join(""), exempt, o.userCjk, true, kw);
      if (used) hits.push(["rewritten", head(strip(orig), 60), head(strip(fixed.text), 60)]);
      hits.push(...fixed.hits.filter((h) => h[0].endsWith("mark") || h[0].endsWith("read")));
      splitParts.get(i)[j] = fixed.text;
    });
  }
  // what neither the model nor the rules could fix: left as written, or flagged with ⚠
  return { text: unmark(join_(st), opts.mark), hits };
}

/**
 * Synchronous local pass. ``final`` true: unfixable leaks are final (⚠ with
 * ``opts.mark``, same as clean() without a rewriter). ``final`` false: unfixable leaks are left as they
 * are and reported in ``unresolved`` (for hosts that ask the model to restate).
 */
export function cleanLocal(text, opts = {}, final = true) {
  const o = prepare(opts);
  const lang = wrongLanguage(text, o.target, o.allowed);
  if (lang) {
    const idxs = wholeLines(text.split("\n"), o.target);
    return { text, hits: idxs.length ? [["whole-mark", lang, head(strip(text.split("\n")[idxs[0]]), 60)]] : [], unresolved: idxs.length > 0, whole: lang };
  }
  const st = firstPass(text, o, final);
  return { text: unmark(join_(st), opts.mark), hits: st.hits, unresolved: st.pending.length > 0, whole: null };
}

/** Hits for a finished answer without rewriting anything. */
export function leaks(text, opts = {}) {
  return cleanLocal(text, opts, true).hits.filter((h) => h[0] !== "rewritten");
}

/** Words of the user's message that may appear in the answer unchanged. */
export function userTokens(msg) {
  msg = msg || "";
  return {
    userWords: new Set(findAll(/[A-Za-z]{2,}/, msg).map((m) => m[0].toLowerCase())),
    userCjk: new Set([...findAll(CJK_RE, msg), ...findAll(HANGUL_RUN, msg)].map((m) => m[0])),
  };
}

const ONCE_RE = /이번(?:만|에는|엔)|이 질문(?:만|에는|은)|just (?:this|once)|this time|今回(?:だけ)?|这次|這次/i;

/** Decide { target, allowed, skip } for this turn; ``memo`` persists between turns. */
function shortLatin(msg, rest) {
  const own = rest ? msg.split(rest).join("") : msg;
  const words = own.match(/[A-Za-zÀ-ÿĀ-ž']+/g) || [];
  return words.length <= 3 && words.reduce((n, w) => n + w.length, 0) < 25 &&
    ![...own].some((ch) => /\p{L}/u.test(ch) && ch.codePointAt(0) > 0x24f);
}

export function resolveTurn(memo, sessionId, msg, def = "en") {
  const key = sessionId || "default";
  memo.sticky ??= {};
  memo.last ??= {};
  const sticky = memo.sticky;
  const last = memo.last;
  msg = stripHostNotes(msg);
  const full = findCmd(FULL_CMD, msg, true);
  if (full.length && !ONCE_RE.test(msg)) sticky[key] = full[full.length - 1];
  const part = set(findCmd(PART_CMD, msg));
  const once = full.length > 0 && ONCE_RE.test(msg);
  if (once) delete sticky[key];
  let [inst, rest] = instructionLanguage(msg);
  // "continue", "ok", "go on": a short English word does not switch the language
  if (inst && inst !== (last[key] || def) && shortLatin(msg, rest)) inst = null;
  const target = full.length ? full[full.length - 1] : sticky[key] || inst || last[key] || def;
  if (!once) last[key] = target;
  const allowed = new Set([target, ...part]);
  let t = stripTechnical(rest.replace(CODE_RE, " "));
  t = t.replace(URL_OR_PATH, " ");
  const c = scriptCounts(t);
  if (c.ko >= 6) allowed.add("ko");
  if (c.ja >= 3) allowed.add("ja");
  if (c.han >= 6 && !c.ja) allowed.add("zh");
  for (const s of Object.keys(SCRIPT_RANGES)) {
    if (c[s] >= 6) {
      const d = detectLanguage(t);
      allowed.add(LANG_SCRIPT[d || ""] === s ? d : SCRIPT_DEFAULT[s]);
    }
  }
  if (findAll(/[A-Za-z]{2,}/, t).length >= 8) allowed.add(latinLang(t));
  const skip = ALLOW_ALL_RE.test(msg);
  for (const d of [sticky, last]) {
    const keys = Object.keys(d);
    if (keys.length > 200) for (const k of keys.slice(0, -100)) delete d[k];
  }
  return { target, allowed, skip };
}
