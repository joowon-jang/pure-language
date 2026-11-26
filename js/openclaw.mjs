// pure-language plugin for OpenClaw (runs inside the Gateway).
//
//   before_prompt_build    pick the turn language, add the short rule to the turn
//   after_tool_call        remember tool output (quoting it verbatim is allowed)
//   before_agent_finalize  a reply in the wrong language or with many leaks gets one more pass;
//                          a stray word or two is rewritten (api.runtime.llm) for delivery
//   before_message_write   store the cleaned reply in the transcript
//   reply_payload_sending  deliver the cleaned reply
//   message_sending        same, for plain channel text
//
// Settings: plugins.entries.pure-language.config in openclaw.json, or ~/.openclaw/pure-language.json.
// Conversation hooks need plugins.entries.pure-language.hooks.allowConversationAccess: true.
import { homedir } from "node:os";
import { join } from "node:path";
import * as E from "./engine.mjs";
import { loadSettings } from "./settings.mjs";
import { Turns, toolText, makeRewriter, cleanAnswer, feedback, worthAsking, engineOpts, unresolvedHits, debug } from "./common.mjs";

const stateDir = () => process.env.OPENCLAW_STATE_DIR || join(homedir(), ".openclaw");
const keyOf = (ctx, event) => event?.sessionKey || ctx?.sessionKey || ctx?.sessionId || "default";
const norm = (s) => String(s || "").trim();

export default {
  id: "pure-language",
  name: "Pure Language",
  description: "Keeps every answer in the conversation's language.",
  register(api) {
    const turns = new Turns();
    const settings = () => loadSettings([join(stateDir(), "pure-language.json")], api.pluginConfig || null);
    let cfg = settings();

    // Direct provider call first; when the agent's model has no API key (for example a
    // Claude CLI login), fall back to an isolated run through the configured CLI runtime.
    let isolatedOnly = false;
    const complete = async (system, user, maxTokens, signal) => {
      const llm = api.runtime?.llm;
      if (!llm || typeof llm.complete !== "function") throw new Error("api.runtime.llm is not available");
      const base = {
        systemPrompt: system,
        messages: [{ role: "user", content: user }],
        purpose: "pure-language.rewrite",
        maxTokens,
        signal,
        ...(cfg.rewrite_model ? { model: cfg.rewrite_model } : {}),
      };
      const ok = (res) => res && res.stopReason !== "error" && typeof res.text === "string" && res.text.trim();
      let firstError;
      if (!isolatedOnly) {
        try {
          const res = await llm.complete({ ...base, temperature: 0 });
          if (ok(res)) return res.text;
          firstError = new Error("completion failed");
        } catch (exc) {
          firstError = exc;
        }
      }
      if (signal?.aborted) throw firstError || new Error("aborted");
      try {
        const res = await llm.complete({
          ...base,
          outputSchema: E.REWRITE_SCHEMA,
          execution: { mode: "isolated-agent-runtime", timeoutMs: Math.max(3000, cfg.timeout * 1000 - 500) },
        });
        if (!ok(res)) throw new Error("isolated completion failed");
        if (firstError && !isolatedOnly) {
          isolatedOnly = true; // remember: this agent only works through its runtime
          debug("rewrite", "direct provider failed, using the agent runtime from now on");
        }
        return res.text;
      } catch (exc) {
        throw firstError ? new Error(`${firstError.message || firstError}; isolated: ${exc.message || exc}`) : exc;
      }
    };
    // host hooks stop after 15 s; leave room for the local work
    const rewriter = () => (cfg.rewrite ? makeRewriter(complete, { ...cfg, timeout: Math.min(cfg.timeout, 12) }) : null);

    /** Cleaned text for an answer: from the finalize cache, else local fixes only. */
    function cachedOrLocal(turn, text) {
      const hit = turn.fixed?.get(norm(text));
      if (hit !== undefined) return hit;
      const local = E.cleanLocal(text, engineOpts(turn, cfg), false);
      return local.text;
    }

    api.on("before_prompt_build", (event, ctx) => {
      cfg = settings();
      const msg = typeof event.currentUserMessage === "string" ? event.currentUserMessage : event.prompt || "";
      const key = keyOf(ctx, event);
      const prev = turns.get(key);
      // the extra pass we asked for: same run, or our own instruction as the prompt
      if (prev && ((ctx?.runId && prev.runId === ctx.runId) || String(event.prompt || "").includes("[pure-language]") ||
          norm(msg).startsWith("[pure-language]"))) {
        debug("before_prompt_build", key, "extra pass, language kept:", prev.target);
        return prev.skip ? undefined : { appendContext: E.guardFor(prev.target, prev.allowed, true) };
      }
      const guard = turns.start(key, msg, cfg);
      const t = turns.get(key);
      t.fixed = new Map();
      t.runId = ctx?.runId;
      debug("before_prompt_build", key, t.target, [...t.allowed].join(","), guard ? "guard" : "skip");
      return guard ? { appendContext: guard } : undefined;
    });

    api.on("after_tool_call", (event, ctx) => {
      turns.addTool(keyOf(ctx, event), toolText([event.result, event.output, event.content]).join("\n"));
    });

    api.on("before_agent_finalize", async (event, ctx) => {
      const turn = turns.get(keyOf(ctx, event));
      const text = event.lastAssistantMessage || "";
      if (!turn || turn.skip || !text) return;
      // The transcript stores the reply before this hook runs, so a reply that leaks is
      // written again by the model in one more pass (the rejected draft is not delivered).
      // cfg.local_fix "first": only what the word tables cannot fix triggers that pass.
      const local = E.cleanLocal(text, engineOpts(turn, cfg), false);
      if (local.hits.length) turn.leaked = true;
      const due = cfg.local_fix === "first" ? unresolvedHits(local.hits) : local.hits.filter((h) => h[0] !== "rewritten");
      debug("before_agent_finalize", keyOf(ctx, event), `hits=${local.hits.length}`, `due=${due.length}`);
      if (!due.length) return;
      if (!event.stopHookActive && !turn.asked && worthAsking(due, cfg)) {
        turn.asked = true;
        debug("before_agent_finalize", "revise");
        return {
          action: "revise",
          reason: "pure-language: reply not in the conversation language",
          retry: {
            instruction: feedback(turn.target, due, true),
            idempotencyKey: `pure-language:${event.runId || event.turnId || Date.now()}`,
            maxAttempts: 1,
          },
        };
      }
      // the extra pass is used up: rewrite for delivery (model, else rules)
      const r = await cleanAnswer(text, turn, cfg, rewriter());
      if (r.text !== text) turn.fixed.set(norm(text), r.text);
      debug("before_agent_finalize", "rewritten for delivery", r.text !== text ? "fixed" : "same");
    });

    // synchronous: cached results, else rule fixes as the last resort (the first draft
    // is left alone, because a leaking draft gets rewritten by the model next)
    api.on("before_message_write", (event, ctx) => {
      const m = event.message;
      if (!m || m.role !== "assistant") return;
      const turn = turns.get(keyOf(ctx, event));
      if (!turn || turn.skip) return;
      if (cfg.local_fix !== "first" && !turn.asked) return;
      let changed = false;
      let content;
      if (typeof m.content === "string") {
        content = cachedOrLocal(turn, m.content);
        changed = content !== m.content;
      } else if (Array.isArray(m.content)) {
        content = m.content.map((b) => {
          if (!b || b.type !== "text" || typeof b.text !== "string") return b;
          const t = cachedOrLocal(turn, b.text);
          if (t === b.text) return b;
          changed = true;
          return { ...b, text: t };
        });
      }
      debug("before_message_write", keyOf(ctx, event), changed ? "changed" : "same");
      return changed ? { message: { ...m, content } } : undefined;
    });

    async function cleanOutgoing(text, key) {
      const turn = turns.get(key) || [...turns.turns.values()].pop();
      if (!turn || turn.skip || !text) return text;
      const hit = turn.fixed?.get(norm(text));
      if (hit !== undefined) return hit;
      const r = await cleanAnswer(text, turn, cfg, rewriter());
      if (r.hits.length) turn.leaked = true;
      return r.text;
    }

    api.on("reply_payload_sending", async (event, ctx) => {
      const p = event.payload;
      if (!p || typeof p.text !== "string" || !p.text) return;
      const text = await cleanOutgoing(p.text, keyOf(ctx, event));
      debug("reply_payload_sending", keyOf(ctx, event), text !== p.text ? "changed" : "same");
      return text !== p.text ? { payload: { ...p, text } } : undefined;
    });

    api.on("message_sending", async (event, ctx) => {
      if (!event.content) return;
      const text = await cleanOutgoing(event.content, keyOf(ctx, event));
      debug("message_sending", keyOf(ctx, event), text !== event.content ? "changed" : "same");
      return text !== event.content ? { content: text } : undefined;
    });
  },
};
