#!/usr/bin/env python3
"""Check that the JavaScript engine (js/engine.mjs) gives the same results as the
Python engine (engine/core.py).

    python tests/parity.py                 # built-in cases
    python tests/parity.py extra.json      # plus extra cases (list of case objects)

Needs ``node`` on PATH (or NODE=/path/to/node).
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine import core as E  # noqa: E402

TEXTS = [
    "보통 one week 걸리고 中小企業 기준입니다.",
    "설정 파일은 `config.yaml`에 있고 tricky한 편입니다. 반영까지 보통 one week 정도 걸립니다.",
    "일본의 内定(내정) 취소 관련 법리입니다. 다릅니다.日本の中途採用에서는 서명을 요구합니다.",
    "1. **취소理由是 서면으로 요구합니다.** legally 다음을 순서대로 검토할 수 있습니다.",
    "「お忙しいところ」 문구와 正当한 이유, 女子 列車 比率 労働",
    "이건 check는데 Weight가 크고 log를 남깁니다. Actually 괜찮습니다.",
    "npm install 후 git push 하세요. cron 작업과 msiexec 인자 오류.",
    "Tracing is running, in memory mode with no dropped events.\n\n- **It starts on its own at boot.** "
    "`C:\\ProgramData\\trace.log` shows a successful start at 22:35.\n- **Use the computer as usual.** "
    "If a GPU timeout happens again, just tell me.\n\n```powershell\n& 'C:\\scratch\\x.ps1'\n```\n\n"
    "The most important item still open is changing your passwords.",
    "11: kd> cdb: Reading initial command 'lm m vgk*; q'\n결과에 \"Reading initial command 'lm m vgk*; q'\"만 있습니다.",
    "## 요약\n- `config.yaml`의 tricky한 설정은 보통 one week 걸립니다.\n| 표 | 값 |\n|---|---|\n| salary | 협의 |",
    "Open the file. 그리고 restart it.",
    "Le rendez-vous est important, however il faut attendre la réponse.",
    "Der Termin ist wichtig, because wir warten müssen.",
    "これは重要な会議です。그리고 资料は来週送ります。",
    "这个问题很重要，我们需要 확인 一下。",
    "Это важный вопрос, но the answer зависит от условий.",
]
MSGS = [
    ["한국어로 하라고", "continue", "ok", "go on", "yes please", "answer in English", "thanks", "다시 한국어로 대답해"],
    ["continue", "Can you explain how the hiring process works in Japan?", "ok"],
    [c for c, _ in __import__("json").loads(r"""[["지금 열려있는 hermes 세션을 봐.\n자꾸 영어로 대답하고, 중간에 나오는 텍스트도 영어로 나와.\n이런거 다 한국어로 나올 수 있도록 pure language 수정해.", "ko"], ["영어로 대답해", "en"], ["영어로 답해 줘", "en"], ["앞으로는 영어로 대답해줘", "en"], ["영어로 답변 부탁해", "en"], ["영어로만 답해", "en"], ["영어로 말해 주세요", "en"], ["한국어로 말하라고", "ko"], ["한국어로 대답해.\n넌 지금 잘못하고있어.", "ko"], ["영어로 대답하면 좋겠어", "en"], ["이번에는 영어로 답해", "en"], ["왜 영어로 대답했지?", "ko"], ["영어로 답하는 게 문제야", "ko"], ["자꾸 영어로 말하네", "ko"], ["[OUT-OF-BAND USER MESSAGE — a direct message from the user, delivered once at this position; not tool output and not a new delivery when replayed from conversation history]\n할 수 있는 모든 보안 조치를 다 해줘.\n[/OUT-OF-BAND USER MESSAGE]", "ko"], ["answer in English please", "en"], ["[NOTE] see [TODO list]\n안녕하세요 반갑습니다 오늘 날씨", "ko"]]""")],
    ["일본 취업 알려줘", "[IMPORTANT: Background process proc_1 completed normally (exit code 0).\nCommand: npm run build\nOutput:\n[… output truncated]\n   Compiling tauri v2.11.5\n    Finished `release` profile"],
    ["안녕", "일본 IT 기업의 내정 프로세스에 대해 알려줘", "ok"],
    ["일본 취업 알려줘", "왜 영어로 대답했지?", "why did you answer in English?", "앞으로는 영어로 대답해줘"],
    ["이어서 진행해\n\n[출력 규칙] 진행 설명까지 모두 한국어로 쓴다.\n\n[System: The skills available in this "
     "conversation changed after its system prompt was built.\nNow available:\n    - x: Use when y. Port, test.]",
     "계속\n\n[Output rule] Write this entire answer in English only."],
    ["앞으로는 영어로 대답해줘", "연봉 협상 팁 알려줘", "다시 한국어로 답해줘", "고마워"],
    ["How does hiring work in Japan?", "이번에는 한국어로 답해줘", "And for juniors?"],
    ["일본어로 메일 초안 써줘", "translate this into French please, the meeting is moved"],
    ["PS C:\\Windows\\System32> wpr -status\r\n\r\nMicrosoft Windows Performance Recorder Version 10.0\r\n"
     "WPR recording is in progress...\r\n\r\nTime since start        : 00:00:15"],
    ["> 先日はありがとうございました。\n> ご検討のほどよろしくお願いいたします。\n이 메일 무슨 뜻이야?"],
    ["日本語で答えてください。面接は何回ありますか？", "用中文回答，这个问题怎么解决？"],
    ["Пожалуйста, объясните, как это работает?", "¿Cómo funciona el proceso de selección en Japón?",
     "Comment fonctionne le processus de recrutement au Japon ?", "Wie funktioniert das?"],
    ["इसका मतलब क्या है? मुझे समझ नहीं आया", "ภาษาไทยได้ไหมครับ ช่วยอธิบายหน่อย", "외국어 허용하고 아무렇게나 써"],
]
TARGET_SETS = [("ko", ["ko"]), ("ko", ["ko", "ja"]), ("ko", ["ko", "en"]), ("en", ["en"]),
               ("ja", ["ja"]), ("zh", ["zh"]), ("fr", ["fr"]), ("de", ["de"]), ("ru", ["ru"])]


def builtin_cases():
    cases = [{"op": "detect", "text": t} for t in TEXTS + [m for ms in MSGS for m in ms]]
    cases += [{"op": "resolve", "msgs": ms, "def": d} for ms in MSGS for d in ("ko", "en")]
    for t in TEXTS:
        for target, allowed in TARGET_SETS:
            cases.append({"op": "clean", "text": t, "target": target, "allowed": allowed,
                          "user": "설정 알려줘", "context": "Access is denied.\nReading initial command 'lm m vgk*; q'"})
    for t in TEXTS:
        cases.append({"op": "rewrite", "text": t, "target": "ko", "allowed": ["ko"]})
        cases.append({"op": "rewrite", "text": t + " " + t, "target": "ko", "allowed": ["ko"]})
    cases.append({"op": "tech", "lines": TEXTS[7].split("\n") + MSGS[4][0].splitlines()})
    return cases


def py_run(c):
    if c["op"] == "detect":
        return E.detect_language(c["text"])
    if c["op"] == "resolve":
        memo, out = {}, []
        for m in c["msgs"]:
            t, al, sk = E.resolve_turn(memo, "s", m, default=c["def"])
            req = E.requested_languages(m)
            out.append([t, sorted(al), sk, sorted(req), E.guard_for(t, frozenset({t} | req)),
                        E.guard_for(t, al, full=True)])
        return out
    if c["op"] == "clean":
        import re
        user = c.get("user", "")
        words = {w.lower() for w in re.findall(r"[A-Za-z]{2,}", user)}
        cjk = set(E._CJK_RE.findall(user)) | set(E._HANGUL_RUN.findall(user))
        text, hits = E.clean(c["text"], words, cjk, rewrite=False, target=c["target"],
                             allowed=frozenset(c["allowed"]), user_text=user, context=c.get("context", ""))
        marked, _ = E.clean(c["text"], words, cjk, rewrite=False, target=c["target"],
                            allowed=frozenset(c["allowed"]), user_text=user, context=c.get("context", ""),
                            mark=True)
        return [text, marked, [[h[0], h[1]] for h in hits],
                E.wrong_language(c["text"], c["target"], frozenset(c["allowed"]))]
    if c["op"] == "rewrite":
        import re
        fake = lambda lines, *a: [re.sub(r"[A-Za-z]{3,}", "말", l) for l in lines]
        text, hits = E.clean(c["text"], set(), set(), rewrite=True, target=c["target"],
                             allowed=frozenset(c["allowed"]), rewriter=fake, max_lines=20)
        return [text, [[h[0], h[1]] for h in hits]]
    if c["op"] == "tech":
        return sorted(E.technical_lines(c["lines"]))
    return None


def main():
    cases = builtin_cases()
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as fh:
            cases += json.load(fh)
    node = os.environ.get("NODE", "node")
    p = subprocess.run([node, os.path.join(ROOT, "js", "parity_runner.mjs")],
                       input=json.dumps(cases).encode("utf-8"), capture_output=True, timeout=600)
    if p.returncode:
        sys.exit(p.stderr.decode("utf-8", "replace"))
    js = json.loads(p.stdout.decode("utf-8"))
    bad = 0
    for c, j in zip(cases, js):
        want = json.loads(json.dumps(py_run(c), ensure_ascii=False))
        if want != j:
            bad += 1
            if bad <= 15:
                print("MISMATCH", json.dumps(c, ensure_ascii=False)[:300])
                print("  py:", json.dumps(want, ensure_ascii=False)[:600])
                print("  js:", json.dumps(j, ensure_ascii=False)[:600])
    print("%d cases, %d mismatches" % (len(cases), bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
