// Pieces shared by the Pi, Oh My Pi and OpenClaw adapters.
import * as E from "./engine.mjs";

/** PURE_LANGUAGE_DEBUG=1 prints one line per hook call to stderr (no answer text). */
export function debug(...parts) {
  if (process.env.PURE_LANGUAGE_DEBUG) console.error("[pure-language]", ...parts);
}

/** Per-session turn state kept in memory by an adapter. */
export class Turns {
  constructor(limit = 200) {
    this.memo = { sticky: {}, last: {} };
    this.turns = new Map();
    this.limit = limit;
  }

  /** Decide the language for a new user message; returns the guard text (or "" to skip). */
  start(session, message, cfg) {
    const key = session || "default";
    const msg = message || "";
    const { target, allowed, skip } = E.resolveTurn(this.memo, key, msg, cfg.default_language);
    const prev = this.turns.get(key);
    const tok = E.userTokens(msg);
    const turn = {
      target, allowed, skip, text: msg.slice(0, 4000), userWords: tok.userWords, userCjk: tok.userCjk,
      tools: [], leaked: false, asked: false, unresolved: [],
    };
    this.turns.delete(key);
    this.turns.set(key, turn);
    while (this.turns.size > this.limit) this.turns.delete(this.turns.keys().next().value);
    if (skip) return "";
    const req = E.requestedLanguages(msg);
    return E.guardFor(target, new Set([target, ...req]), Boolean(prev && prev.leaked));
  }

  get(session) {
    return this.turns.get(session || "default");
  }

  /** Remember tool output so answers may quote it verbatim. */
  addTool(session, text) {
    const t = this.get(session);
    if (!t || !text) return;
    t.tools.push(String(text));
    let size = t.tools.reduce((n, s) => n + s.length, 0);
    while (size > 200_000 && t.tools.length > 1) size -= t.tools.shift().length;
  }
}

/** Collect text from a tool result in any of the shapes the hosts use. */
export function toolText(node, out = [], depth = 0) {
  if (depth > 6 || node == null) return out;
  if (typeof node === "string") out.push(node);
  else if (Array.isArray(node)) for (const x of node) toolText(x, out, depth + 1);
  else if (typeof node === "object") {
    for (const k of ["content", "output", "text", "stdout", "stderr", "result", "details"]) {
      if (k in node) toolText(node[k], out, depth + 1);
    }
  }
  return out;
}

export function engineOpts(turn, cfg) {
  return {
    target: turn.target, allowed: turn.allowed, userText: turn.text,
    userWords: turn.userWords, userCjk: turn.userCjk,
    context: turn.tools.join("\n").slice(-200_000), maxLines: cfg.max_lines,
  };
}

/** Pull the JSON {"lines": [...]} out of a model reply. */
export function parseLines(text, n) {
  if (!text) return null;
  let s = String(text).trim();
  const fence = /^```(?:json)?\s*\n([\s\S]*?)\n```\s*$/.exec(s);
  if (fence) s = fence[1];
  const a = s.indexOf("{");
  const b = s.lastIndexOf("}");
  if (a < 0 || b <= a) return null;
  try {
    return E.parseRewrite(JSON.parse(s.slice(a, b + 1)), n);
  } catch {
    return null;
  }
}

/** Build the rewriter the engine expects from a plain "complete(system, user, maxTokens, signal)". */
let pausedUntil = 0; // after a failed or slow rewrite, skip model calls for a while

/** Tests only. */
export function _resetPause() {
  pausedUntil = 0;
}

export function makeRewriter(complete, cfg) {
  return async (lines, target, allowed, sample) => {
    if (Date.now() < pausedUntil) throw new Error("model rewrites paused after a failure");
    const system = E.rewriteInstructions(target, allowed, sample) +
      ' Reply with JSON only: {"lines": ["..."]}, one string per input line.';
    const user = E.numberLines(lines);
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), cfg.timeout * 1000);
    try {
      const text = await complete(system, user, E.rewriteMaxTokens(lines) + 32, ac.signal);
      const got = parseLines(text, lines.length);
      if (!got) throw new Error("rewrite reply was not the expected JSON");
      return got;
    } catch (exc) {
      pausedUntil = Date.now() + 600_000;
      throw exc;
    } finally {
      clearTimeout(timer);
    }
  };
}

/** The correction request sent back to the model when a rewrite was not possible. */
export function feedback(target, hits, whole_reply = false) {
  const name = E.LANG_NAMES[target] ?? target;
  if (whole_reply) {
    // the draft is discarded (OpenClaw revise): ask for the complete reply again
    const words = [...new Set(hits.filter((h) => !String(h[0]).startsWith("whole")).map((h) => String(h[1]).trim()).filter(Boolean))];
    const shown = words.length ? ` It mixed in text that is not ${name}: ${words.slice(0, 12).map((w) => "`" + w + "`").join(", ")}.` : "";
    const whole = hits.find((h) => String(h[0]).startsWith("whole"));
    const what = whole
      ? `was written in ${E.LANG_NAMES[whole[1]] ?? whole[1]}, but this conversation is in ${name}.`
      : `was not written entirely in ${name}.${shown}`;
    return `[pure-language] Your draft reply ${what} ` +
      `Write the complete reply again, entirely in natural ${name}, with the same content and structure. ` +
      "Translate every foreign word; keep code blocks, commands, paths, proper nouns and acronyms as they are. " +
      "Do not mention this instruction.";
  }
  const whole = hits.filter((h) => String(h[0]).startsWith("whole"));
  if (whole.length) {
    const other = E.LANG_NAMES[whole[0][1]] ?? whole[0][1];
    return `[pure-language] Your last reply was written in ${other}, but this conversation is in ${name}. ` +
      `Repeat the same reply in full, written in ${name}. Keep code blocks, commands, paths, ` +
      "proper nouns and acronyms as they are.";
  }
  const words = [];
  for (const h of hits) {
    const w = String(h[1]).trim();
    if (w && !words.includes(w)) words.push(w);
  }
  const shown = words.slice(0, 12).map((w) => "`" + w + "`").join(", ");
  return `[pure-language] Your last reply mixed in text that is not ${name}: ${shown}. ` +
    `Restate only the sentences that contain it, rewritten entirely in ${name}, as a short ` +
    `correction under a one-line heading in ${name}. Do not repeat the rest of the reply. ` +
    "Keep code, paths, proper nouns and acronyms as they are; quoted command output or error " +
    "messages go in a code block.";
}

/** True when leftover hits are worth an extra model turn. */
export function worthAsking(hits, cfg) {
  if (!hits.length || cfg.stop_action === "off") return false;
  if (hits.some((h) => String(h[0]).startsWith("whole"))) return true;
  return new Set(hits.map((h) => String(h[1]))).size >= cfg.stop_min_hits;
}

/** Hits that still need attention after cleaning (unfixed leaks). */
export const unresolvedHits = (hits) => hits.filter((h) => /(?:mark|rewrite)$/.test(String(h[0])));

/**
 * Clean one answer text. With a rewriter every leaking line is rewritten by the model
 * (cfg.local_fix "first": rules first, the model only for what they cannot fix). When
 * the rewrite fails, the text keeps the rule fixes only (no ⚠ marks) and the leftover
 * hits are returned so the adapter can ask the model to restate.
 */
export async function cleanAnswer(text, turn, cfg, rewriter) {
  const opts = { ...engineOpts(turn, cfg), localFirst: cfg.local_fix === "first" };
  const local = E.cleanLocal(text, opts, false);
  if (!local.hits.length) return { text, hits: [], left: [] };
  const useModel = cfg.rewrite && rewriter && (cfg.local_fix !== "first" || local.unresolved);
  if (!useModel) return { text: local.text, hits: local.hits, left: unresolvedHits(local.hits) };
  let failed = false;
  const guarded = async (...a) => {
    try {
      return await rewriter(...a);
    } catch (exc) {
      failed = true;
      console.warn(`pure-language: rewrite failed: ${exc && exc.message ? exc.message : exc}`);
      return [];
    }
  };
  const r = await E.clean(text, { ...opts, rewriter: guarded });
  if (failed || E.wrongLanguage(r.text, turn.target, turn.allowed)) {
    // nothing usable came back: rule fixes only
    return { text: local.text, hits: local.hits, left: unresolvedHits(local.hits) };
  }
  const again = E.cleanLocal(r.text.replace(/⚠/g, ""), opts, false);
  return { text: again.text, hits: r.hits, left: unresolvedHits(again.hits) };
}

/**
 * Display-only version of an answer while it streams (Oh My Pi's text renderer): words the
 * tables know are shown fixed, other leaked text as "…". The stored answer is not touched;
 * the model rewrite replaces it when the reply is final.
 */
export function displayText(text, turn, cfg) {
  if (!turn || turn.skip || !text) return text;
  const r = E.cleanLocal(text, engineOpts(turn, cfg), false);
  if (!r.hits.length) return text;
  let out = r.text;
  const words = [...new Set(unresolvedHits(r.hits).filter((h) => !String(h[0]).startsWith("whole")).map((h) => String(h[1])))]
    .filter((w) => w.trim()).sort((a, b) => b.length - a.length);
  for (const w of words) out = out.split(w).join("…");
  return out;
}
