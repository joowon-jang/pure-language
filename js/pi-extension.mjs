// pure-language extension for Pi (badlogic/pi-mono) and Oh My Pi (omp).
//
//   before_agent_start   pick the turn language, add the short rule as a hidden message
//   tool_result          remember tool output (quoting it verbatim is allowed)
//   assistant_message    Oh My Pi: clean each finished assistant message before it is kept
//   message_end          Pi: same, by returning the replacement message
//   session_stop         Oh My Pi: if a rewrite was impossible, ask for a short restatement
//   agent_before_settle  Pi: same
//
// Settings: <agent dir>/pure-language.json (Pi: ~/.pi/agent, Oh My Pi: ~/.omp/agent).
import { homedir } from "node:os";
import { join } from "node:path";
import { loadSettings } from "./settings.mjs";
import { Turns, toolText, makeRewriter, cleanAnswer, feedback, worthAsking, debug, displayText } from "./common.mjs";

// The hosts resolve their own packages only for literal import specifiers, so each
// name is spelled out (a loop over a list of names fails to resolve).
const AI_LOADERS = [
  () => import("@oh-my-pi/pi-ai"),
  () => import("@earendil-works/pi-ai"),
  () => import("@mariozechner/pi-ai"),
];
let aiModule;

/** Tests only: provide the model library instead of importing it from the host. */
export function _setAiForTests(m) {
  aiModule = m;
}

async function loadAi() {
  if (aiModule !== undefined) return aiModule;
  aiModule = null;
  for (const load of AI_LOADERS) {
    try {
      const m = await load();
      if (typeof m.completeSimple === "function" || typeof m.complete === "function") {
        aiModule = m;
        break;
      }
    } catch {
      /* not this host */
    }
  }
  return aiModule;
}

function agentDirs() {
  const home = homedir();
  const dirs = [];
  if (process.env.PI_CODING_AGENT_DIR) dirs.push(process.env.PI_CODING_AGENT_DIR);
  dirs.push(join(home, ".omp", "agent"), join(home, ".pi", "agent"));
  return dirs;
}

const sessionOf = (ctx) => {
  try {
    return ctx?.sessionManager?.getSessionId?.() || "default";
  } catch {
    return "default";
  }
};

const textOf = (message) =>
  Array.isArray(message?.content)
    ? message.content.filter((b) => b && b.type === "text").map((b) => b.text || "").join("\n")
    : typeof message?.content === "string" ? message.content : "";

/** Models to try for the rewrite, best first: the setting, Oh My Pi's cheap "smol" role, the session model. */
function candidateModels(ctx, spec) {
  const out = [];
  const add = (m) => {
    if (m && !out.some((x) => x.provider === m.provider && x.id === m.id)) out.push(m);
  };
  const tryAdd = (fn) => {
    try {
      add(fn());
    } catch { /* not available on this host */ }
  };
  if (spec) {
    tryAdd(() => ctx.models?.resolve?.(spec));
    const [provider, ...rest] = spec.split("/");
    if (rest.length) tryAdd(() => ctx.modelRegistry?.find?.(provider, rest.join("/")));
  }
  if (!spec) tryAdd(() => ctx.models?.resolve?.("@smol"));
  add(ctx.model);
  return out;
}

async function authFor(ctx, model, session) {
  const reg = ctx.modelRegistry;
  if (!reg) return {};
  if (typeof reg.getProviderAuth === "function") {
    const a = await reg.getProviderAuth(model.provider);
    return { apiKey: a?.apiKey, headers: a?.headers };
  }
  if (typeof reg.getApiKey === "function") return { apiKey: await reg.getApiKey(model, session) };
  if (typeof reg.getApiKeyForProvider === "function") return { apiKey: await reg.getApiKeyForProvider(model.provider) };
  return {};
}

/** Final assistant message of a pi-ai event stream. */
async function finalMessage(stream) {
  if (stream && typeof stream.result === "function") return await stream.result();
  let last = null;
  for await (const ev of stream) {
    if (ev?.type === "done") return ev.message;
    if (ev?.type === "error") throw new Error(ev.error?.errorMessage || "stream error");
    if (ev?.partial) last = ev.partial;
  }
  return last;
}

async function completeWith(ctx, model, session, system, user, maxTokens, signal) {
  const auth = await authFor(ctx, model, session);
  const context = {
    systemPrompt: system,
    messages: [{ role: "user", content: user, timestamp: Date.now() }],
  };
  const opts = { ...auth, maxTokens, signal };
  let res;
  const ai = await loadAi();
  if (ai) {
    res = await (ai.completeSimple ?? ai.complete)(model, context, opts);
  } else {
    // Pi: extensions cannot import the model library dynamically, but the registry
    // hands out the provider itself
    const provider = ctx.modelRegistry?.getProvider?.(model.provider);
    if (!provider || typeof provider.streamSimple !== "function") throw new Error("model library not available to extensions");
    res = await finalMessage(await provider.streamSimple(model, context, opts));
  }
  if (res?.stopReason === "error" || res?.stopReason === "aborted") throw new Error(res.errorMessage || res.stopReason);
  return textOf(res);
}

function rewriterFor(ctx, cfg, session) {
  return makeRewriter(async (system, user, maxTokens, signal) => {
    const models = candidateModels(ctx, cfg.rewrite_model);
    if (!models.length) throw new Error("no model selected");
    let last;
    for (const model of models) {
      if (signal?.aborted) break;
      try {
        return await completeWith(ctx, model, session, system, user, maxTokens, signal);
      } catch (exc) {
        last = exc;
        debug("rewrite with", `${model.provider}/${model.id}`, "failed:", exc && exc.message ? exc.message : exc);
      }
    }
    throw last || new Error("rewrite timed out");
  }, cfg);
}

export default function pureLanguage(pi) {
  const turns = new Turns();
  let cfg = loadSettings(agentDirs().map((d) => join(d, "pure-language.json")));
  let ompRewrites = false; // set once Oh My Pi's assistant_message fires
  let current = null;      // the turn being answered (renderers get no session context)

  // Oh My Pi: display-only renderer, also called on every streaming frame, so leaked words
  // never appear on screen (needs the extensionRenderers.assistantText setting).
  if (typeof pi.registerAssistantTextRenderer === "function") {
    const memo = { text: null, out: null };
    pi.registerAssistantTextRenderer(({ text }) => {
      if (!current || typeof text !== "string" || !text) return undefined;
      if (memo.text === text) return memo.out;
      let out;
      try {
        out = displayText(text, current, cfg);
      } catch {
        out = text;
      }
      memo.text = text;
      memo.out = out === text ? undefined : out;
      return memo.out;
    });
  }

  pi.on("before_agent_start", async (event, ctx) => {
    if (event.messageType === "custom" || event.reason === "revive") return;
    // our own restatement request comes back as the next prompt: keep the turn's language
    if (String(event.prompt || "").trimStart().startsWith("[pure-language]")) return;
    cfg = loadSettings(agentDirs().map((d) => join(d, "pure-language.json")));
    const guard = turns.start(sessionOf(ctx), event.prompt || "", cfg);
    const t = turns.get(sessionOf(ctx));
    current = t;
    debug("before_agent_start", t.target, [...t.allowed].join(","), guard ? "guard" : "skip");
    if (!guard) return;
    return { message: { customType: "pure-language", content: guard, display: false } };
  });

  pi.on("tool_result", async (event, ctx) => {
    turns.addTool(sessionOf(ctx), toolText(event.content).join("\n"));
  });

  async function fix(message, ctx) {
    if (!message || message.role !== "assistant" || !Array.isArray(message.content)) return null;
    const session = sessionOf(ctx);
    const turn = turns.get(session);
    if (!turn || turn.skip) return null;
    const rewriter = rewriterFor(ctx, cfg, session);
    let changed = false;
    const content = [];
    for (const block of message.content) {
      if (block && block.type === "text" && typeof block.text === "string" && block.text.trim()) {
        const r = await cleanAnswer(block.text, turn, cfg, rewriter);
        if (r.hits.length) turn.leaked = true;
        if (r.left.length) turn.unresolved.push(...r.left);
        if (r.text !== block.text) {
          changed = true;
          content.push({ ...block, text: r.text });
          continue;
        }
      }
      content.push(block);
    }
    debug("assistant message", `changed=${changed}`, `left=${turn.unresolved.length}`);
    return changed ? content : null;
  }

  // Oh My Pi: replaces the finished message for history, display and the next request.
  pi.on("assistant_message", async (event, ctx) => {
    ompRewrites = true;
    const content = await fix(event.message, ctx);
    return content ? { content } : undefined;
  });

  // Pi: message_end may return a replacement message (same role).
  pi.on("message_end", async (event, ctx) => {
    if (ompRewrites) return;
    const content = await fix(event.message, ctx);
    return content ? { message: { ...event.message, content } } : undefined;
  });

  const askAgain = (ctx) => {
    const turn = turns.get(sessionOf(ctx));
    if (!turn || turn.asked || !worthAsking(turn.unresolved, cfg)) return null;
    turn.asked = true;
    debug("restatement requested");
    const msg = feedback(turn.target, turn.unresolved);
    turn.unresolved = [];
    return msg;
  };

  // Oh My Pi
  pi.on("session_stop", async (event, ctx) => {
    if (event.stop_hook_active) return;
    const msg = askAgain(ctx);
    return msg ? { continue: true, additionalContext: msg } : undefined;
  });

  // Pi
  pi.on("agent_before_settle", async (event, ctx) => {
    const msg = askAgain(ctx);
    if (!msg) return;
    return {
      entries: [...(event.entries || []), { type: "custom_message", customType: "pure-language", content: msg, display: false }],
      continue: true,
    };
  });
}
