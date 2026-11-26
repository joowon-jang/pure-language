// Settings shared by the JavaScript adapters (mirrors engine/settings.py).
import { readFileSync } from "node:fs";

export const DEFAULTS = {
  default_language: "en", // used when a message is too short to detect and no earlier turn exists
  rewrite: true,          // rewrite unfixable lines with a model before the answer is kept
  rewrite_model: "",      // Pi / Oh My Pi: model for rewriting ("provider/id"); empty = current model
                          // OpenClaw: model ref for api.runtime.llm.complete; empty = agent default
  timeout: 15,            // seconds for the rewrite call
  max_lines: 20,          // most lines per rewrite call
  stop_action: "rewrite", // when a rewrite is impossible: "rewrite" asks the model to restate, "off" does not
  stop_min_hits: 1,       // distinct leaked words before asking for a restatement (wrong-language reply always)
  local_fix: "fallback",  // "fallback": the model rewrites every leaking line, rules only when it cannot;
                          // "first": word tables and Hanja readings first (cheaper, can read awkwardly)
  log: false,             // reserved; the JavaScript adapters do not write logs
};

/** Merge JSON files (later wins) and an inline object over DEFAULTS. */
export function loadSettings(paths = [], inline = null) {
  const cfg = { ...DEFAULTS };
  const env = process.env.PURE_LANGUAGE_CONFIG;
  const merge = (data) => {
    if (data && typeof data === "object" && !Array.isArray(data)) {
      for (const [k, v] of Object.entries(data)) if (k in DEFAULTS) cfg[k] = v;
    }
  };
  for (const p of [...paths, ...(env ? [env] : [])]) {
    try {
      merge(JSON.parse(readFileSync(p, "utf8")));
    } catch {
      /* missing or invalid file: ignore */
    }
  }
  merge(inline);
  const int = (v, d) => (Number.isFinite(Number(v)) ? Math.trunc(Number(v)) : d);
  cfg.timeout = Math.max(5, Math.min(25, int(cfg.timeout, DEFAULTS.timeout)));
  cfg.max_lines = Math.max(1, int(cfg.max_lines, DEFAULTS.max_lines));
  cfg.stop_min_hits = Math.max(1, int(cfg.stop_min_hits, DEFAULTS.stop_min_hits));
  if (typeof cfg.default_language !== "string" || !cfg.default_language) cfg.default_language = "en";
  if (typeof cfg.rewrite_model !== "string") cfg.rewrite_model = "";
  if (cfg.local_fix !== "fallback" && cfg.local_fix !== "first") cfg.local_fix = "fallback";
  return cfg;
}
