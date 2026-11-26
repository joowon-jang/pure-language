// Adapter tests with fake hosts: node --test tests/js/
import { test } from "node:test";
import assert from "node:assert/strict";
import piExt, { _setAiForTests } from "../../js/pi-extension.mjs";
import openclaw from "../../js/openclaw.mjs";
import * as E from "../../js/engine.mjs";
import { _resetPause } from "../../js/common.mjs";
import { beforeEach } from "node:test";

beforeEach(() => _resetPause());

process.env.PURE_LANGUAGE_CONFIG = "";

function fakePi() {
  const h = {};
  return { h, on: (name, fn) => ((h[name] ??= []).push(fn)), registerAssistantTextRenderer: (fn) => (h.renderer = fn) };
}
const ctx = (id = "s1") => ({
  sessionManager: { getSessionId: () => id },
  model: { provider: "fake", id: "m" },
  modelRegistry: { getApiKey: async () => "k" },
});
const fakeAi = (fn) => ({
  completeSimple: async (model, context) => ({ role: "assistant", stopReason: "stop", content: [{ type: "text", text: fn(context) }] }),
});
const linesOf = (context) => context.messages[0].content.split("\n").map((l) => l.replace(/^\d+\t/, ""));

test("engine: detect, guard and local fix", async () => {
  assert.equal(E.detectLanguage("일본 IT 기업의 내정 프로세스에 대해 알려줘"), "ko");
  const r = E.cleanLocal("보통 中小企業 기준입니다.", { target: "ko" });
  assert.equal(r.text, "보통 중소기업 기준입니다.");
});

test("pi family: guard as hidden message, then rewrite through the host model", async () => {
  _setAiForTests(fakeAi((c) => JSON.stringify({ lines: linesOf(c).map(() => "보통 일주일 정도 걸리고 까다로운 편입니다.") })));
  const pi = fakePi();
  piExt(pi);
  const c = ctx();
  const start = await pi.h.before_agent_start[0]({ type: "before_agent_start", reason: "prompt", prompt: "일본 IT 기업 채용 절차 알려줘", systemPrompt: [] }, c);
  assert.equal(start.message.customType, "pure-language");
  assert.equal(start.message.display, false);
  assert.match(start.message.content, /한국어/);
  const msg = { role: "assistant", content: [{ type: "text", text: "보통 one week 정도 걸리고 tricky한 편입니다." }, { type: "toolCall", id: "t" }] };
  const out = await pi.h.assistant_message[0]({ type: "assistant_message", message: msg }, c);
  assert.equal(out.content.length, 2);
  assert.equal(out.content[0].text, "보통 일주일 정도 걸리고 까다로운 편입니다.");
  assert.deepEqual(out.content[1], { type: "toolCall", id: "t" });
  // Oh My Pi handled it: message_end must not run a second pass
  assert.equal(await pi.h.message_end[0]({ type: "message_end", message: msg }, c), undefined);
  const stop = await pi.h.session_stop[0]({ type: "session_stop", stop_hook_active: false }, c);
  assert.equal(stop, undefined);
});

test("pi family: a word the tables could fix still goes to the model", async () => {
  const seen = [];
  _setAiForTests(fakeAi((c) => { seen.push(...linesOf(c)); return JSON.stringify({ lines: ["보통 서류 전형을 거칩니다."] }); }));
  const pi = fakePi();
  piExt(pi);
  const c = ctx("s5");
  await pi.h.before_agent_start[0]({ reason: "prompt", prompt: "일본 채용 절차 알려줘", systemPrompt: [] }, c);
  const out = await pi.h.assistant_message[0]({ message: { role: "assistant", content: [{ type: "text", text: "보통 書類選考를 거칩니다." }] } }, c);
  assert.deepEqual(seen, ["보통 書類選考를 거칩니다."]);
  assert.equal(out.content[0].text, "보통 서류 전형을 거칩니다.");
});

test("oh my pi: streaming display hides leaked words", async () => {
  _setAiForTests(null);
  const pi = fakePi();
  piExt(pi);
  const c = ctx("s6");
  assert.equal(pi.h.renderer({ text: "오늘 meeting에서", streaming: true }), undefined); // no turn yet
  await pi.h.before_agent_start[0]({ reason: "prompt", prompt: "회의 결과 알려줘", systemPrompt: [] }, c);
  assert.equal(pi.h.renderer({ text: "오늘 schedule을 정하고 tricky한 부분", streaming: true }), "오늘 일정을 정하고 …한 부분");
  assert.equal(pi.h.renderer({ text: "오늘 일정을 정했습니다.", streaming: false }), undefined);
});

test("pi: message_end returns a replacement message", async () => {
  _setAiForTests(fakeAi((c) => JSON.stringify({ lines: linesOf(c).map(() => "결과를 확인했습니다.") })));
  const pi = fakePi();
  piExt(pi);
  const c = ctx("s2");
  await pi.h.before_agent_start[0]({ type: "before_agent_start", messageType: "user", prompt: "결과 확인해줘", systemPrompt: "" }, c);
  const msg = { role: "assistant", content: [{ type: "text", text: "결과를 check했습니다 legally." }] };
  const out = await pi.h.message_end[0]({ type: "message_end", message: msg }, c);
  assert.equal(out.message.role, "assistant");
  assert.equal(out.message.content[0].text, "결과를 확인했습니다.");
});

test("pi family: no model available -> local fixes, then one restatement request", async () => {
  _setAiForTests(null);
  const pi = fakePi();
  piExt(pi);
  const c = ctx("s3");
  await pi.h.before_agent_start[0]({ reason: "prompt", prompt: "일본 취업 절차를 알려줘", systemPrompt: [] }, c);
  const text = "日本の中途採用에서는 承諾書를 요구합니다. 취소理由是 legally 검토 needed 합니다.";
  const out = await pi.h.message_end[0]({ message: { role: "assistant", content: [{ type: "text", text }] } }, c);
  assert.ok(!out || !out.message.content[0].text.includes("⚠"));
  const r = await pi.h.agent_before_settle[0]({ entries: [] }, c);
  assert.equal(r.continue, true);
  assert.equal(r.entries[0].customType, "pure-language");
  assert.match(r.entries[0].content, /Restate only the sentences/);
  // only once per turn
  assert.equal(await pi.h.agent_before_settle[0]({ entries: [] }, c), undefined);
});

test("pi family: tool output quoted verbatim stays", async () => {
  _setAiForTests(null);
  const pi = fakePi();
  piExt(pi);
  const c = ctx("s4");
  await pi.h.before_agent_start[0]({ reason: "prompt", prompt: "명령 실행해서 결과 보여줘", systemPrompt: [] }, c);
  await pi.h.tool_result[0]({ content: [{ type: "text", text: "Access is denied because the service is still running." }] }, c);
  const text = "결과는 \"Access is denied because the service is still running.\"입니다.";
  const out = await pi.h.message_end[0]({ message: { role: "assistant", content: [{ type: "text", text }] } }, c);
  assert.equal(out, undefined);
});

function fakeOpenClaw(complete) {
  const h = {};
  const api = { pluginConfig: {}, runtime: { llm: { complete } }, on: (n, fn) => ((h[n] ??= []).push(fn)) };
  openclaw.register(api);
  return h;
}

test("openclaw: guard, outgoing rewrite and revise for a wrong-language reply", async () => {
  const h = fakeOpenClaw(async ({ messages }) => ({
    stopReason: "stop",
    text: JSON.stringify({ lines: messages[0].content.split("\n").map(() => "보통 일주일 정도 걸립니다.") }),
  }));
  const c = { sessionKey: "agent:main:main" };
  const g = h.before_prompt_build[0]({ prompt: "x", currentUserMessage: "일본 IT 기업 채용 기간 알려줘", messages: [] }, c);
  assert.match(g.appendContext, /한국어/);
  const sent = await h.message_sending[0]({ to: "u", content: "보통 one week 정도 걸립니다." }, c);
  assert.equal(sent.content, "보통 일주일 정도 걸립니다.");
  const english = "The hiring process in Japan usually takes about one week from the final interview to the offer letter, and the company then asks for a signed acceptance.";
  const fin = await h.before_agent_finalize[0]({ sessionId: "x", sessionKey: "agent:main:main", stopHookActive: false, lastAssistantMessage: english }, c);
  assert.equal(fin.action, "revise");
  assert.match(fin.retry.instruction, /written in English/);
  assert.equal(await h.before_agent_finalize[0]({ sessionKey: "agent:main:main", stopHookActive: false, lastAssistantMessage: english }, c), undefined);
  // the extra pass keeps the turn language even though our instruction is English
  const again = h.before_prompt_build[0]({ prompt: fin.retry.instruction, currentUserMessage: "", messages: [] }, c);
  assert.match(again.appendContext, /한국어/);
});

test("openclaw: one stray word -> whole reply rewritten by the model; rules only on the extra pass", async () => {
  const h = fakeOpenClaw(async () => { throw new Error("no auth"); });
  const c = { sessionKey: "k3" };
  h.before_prompt_build[0]({ prompt: "일본 채용 절차 알려줘", messages: [] }, c);
  const draft = "오늘 meeting에서 schedule을 정했습니다.";
  const msg = { role: "assistant", content: [{ type: "text", text: draft }] };
  // first draft: stored as is, because the model rewrites it next
  assert.equal(h.before_message_write[0]({ message: msg, sessionKey: "k3" }, c), undefined);
  const fin = await h.before_agent_finalize[0]({ sessionKey: "k3", stopHookActive: false, lastAssistantMessage: draft }, c);
  assert.equal(fin.action, "revise");
  assert.match(fin.retry.instruction, /Write the complete reply again/);
  // the extra pass still leaks: rule fixes are the last resort, no second revise
  h.before_prompt_build[0]({ prompt: fin.retry.instruction, currentUserMessage: "", messages: [] }, c);
  const out = h.before_message_write[0]({ message: msg, sessionKey: "k3" }, c);
  assert.equal(out.message.content[0].text, "오늘 meeting에서 일정을 정했습니다.");
  assert.equal(await h.before_agent_finalize[0]({ sessionKey: "k3", stopHookActive: false, lastAssistantMessage: draft }, c), undefined);
});

test("openclaw: rewrite failure leaves no warning marks", async () => {
  const h = fakeOpenClaw(async () => { throw new Error("no auth"); });
  const c = { sessionKey: "k2" };
  h.before_prompt_build[0]({ prompt: "일본 채용 절차 알려줘", messages: [] }, c);
  const sent = await h.message_sending[0]({ to: "u", content: "보통 中小企業은 one week 걸립니다." }, c);
  assert.equal(sent.content, "보통 중소기업은 one week 걸립니다.");
});
