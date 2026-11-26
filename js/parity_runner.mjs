// Reads cases (JSON) on stdin, runs them through js/engine.mjs, writes results to stdout.
import * as E from "./engine.mjs";

const chunks = [];
for await (const c of process.stdin) chunks.push(c);
const cases = JSON.parse(Buffer.concat(chunks).toString("utf8"));
const sorted = (s) => [...s].sort();
const out = [];
for (const c of cases) {
  try {
    if (c.op === "detect") out.push(E.detectLanguage(c.text));
    else if (c.op === "resolve") {
      const memo = {};
      const res = c.msgs.map((m) => {
        const r = E.resolveTurn(memo, "s", m, c.def);
        return [r.target, sorted(r.allowed), r.skip, sorted(E.requestedLanguages(m)),
                E.guardFor(r.target, new Set([r.target, ...E.requestedLanguages(m)])),
                E.guardFor(r.target, r.allowed, true)];
      });
      out.push(res);
    } else if (c.op === "clean") {
      const tok = E.userTokens(c.user || "");
      const opts = { target: c.target, allowed: c.allowed, userText: c.user || "",
        userWords: tok.userWords, userCjk: tok.userCjk, context: c.context || "" };
      const r = E.cleanLocal(c.text, opts, true);
      const marked = E.cleanLocal(c.text, { ...opts, mark: true }, true);
      out.push([r.text, marked.text, r.hits.map((h) => [h[0], h[1]]), E.wrongLanguage(c.text, c.target, new Set(c.allowed))]);
    } else if (c.op === "rewrite") {
      const fake = async (lines) => lines.map((l) => l.replace(/[A-Za-z]{3,}/g, "말"));
      const r = await E.clean(c.text, { target: c.target, allowed: c.allowed, rewriter: fake, maxLines: 20 });
      out.push([r.text, r.hits.map((h) => [h[0], h[1]])]);
    } else if (c.op === "tech") out.push(sorted(E.technicalLines(c.lines)).map(Number).sort((a, b) => a - b));
    else out.push(null);
  } catch (e) {
    out.push({ error: String(e && e.stack || e) });
  }
}
process.stdout.write(JSON.stringify(out));
