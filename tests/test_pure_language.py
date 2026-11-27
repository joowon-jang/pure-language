"""Regression tests for pure-language (standard library only, no model calls).

    python -m unittest discover -s tests        # from the repository root
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["HERMES_HOME"] = tempfile.mkdtemp(prefix="pl-hermes-")   # isolated settings and logs

from engine import core as E  # noqa: E402
from engine import settings as S  # noqa: E402

# load the Hermes adapter as a package, the way Hermes does
_spec = importlib.util.spec_from_file_location("pure_language_hermes", os.path.join(ROOT, "__init__.py"),
                                               submodule_search_locations=[ROOT])
H = importlib.util.module_from_spec(_spec)
sys.modules["pure_language_hermes"] = H
_spec.loader.exec_module(H)

_MEMO = {}


def R(session, msg):
    return E.resolve_turn(_MEMO, session, msg, default="ko")


def c(text, target="ko", allowed=None):
    return E.clean(text, rewrite=False, target=target, allowed=frozenset(allowed or {target}), mark=True)[0]


class PathsStayIntact(unittest.TestCase):
    """File paths, URLs and MEDIA: lines name real files; the cleaner must not touch them."""

    def test_media_line_and_paths_unchanged(self):
        text = ("수정본입니다.\n\nMEDIA:C:\\Users\\me\\Downloads\\【KOREC専用】履歴書_JANG_JOOWON_v2.xlsx\n"
                "- 원본 C:\\Users\\x\\【KOREC専用】履歴書.xlsx 와 ~/.hermes/専用/a.txt 는 그대로입니다.")
        out, hits = E.clean(text, rewrite=False, target="ko", allowed=frozenset({"ko"}))
        self.assertEqual(out, text)
        self.assertEqual(hits, [])

    def test_leak_outside_the_path_is_still_fixed(self):
        out = c("파일 C:\\x\\専用.txt 는 専用 폴더에 있습니다.")
        self.assertIn("C:\\x\\専用.txt", out)
        self.assertNotIn(" 専用 폴더", out)

    def test_masked_path_keeps_the_line_length(self):
        # a long path used to keep the Korean words more than 40 characters away; masking it as one
        # character pulled them close and an English error message read as a leak
        line = ("> 드라이버 파일 삭제 실패(재부팅 후 다시): Access to the path "
                "'C:\\Windows\\System32\\AMDRyzenMasterDriver.sys' is denied.")
        words = [h[1] for h in E.clean(line, rewrite=False, target="ko", allowed=frozenset({"ko"}))[1]]
        self.assertNotIn("is denied", words)      # beyond the path: more than 40 characters from Hangul
        self.assertNotIn("is", words)

    def test_spans(self):
        cases = {
            "자세한 건 /usr/local/bin/hermes 참고.": ["/usr/local/bin/hermes"],
            "주소는 https://example.com/a(b) 입니다.": ["https://example.com/a(b)"],
            "(C:\\Users\\x\\a.txt) 확인": ["C:\\Users\\x\\a.txt"],
            "\\\\srv\\share\\a.pdf 와 ..\\x\\y": ["\\\\srv\\share\\a.pdf", "..\\x\\y"],
            "예/아니오, and/or, 1/2/3": [],
        }
        for line, want in cases.items():
            self.assertEqual([line[a:b] for a, b in E.path_spans(line)], want, line)

    def test_rewrite_must_keep_paths(self):
        orig = "파일 C:\\x\\専用.txt 를 check 하세요"
        self.assertFalse(E._structure_ok(orig, "파일 C:\\x\\전용.txt 를 확인하세요"))
        self.assertTrue(E._structure_ok(orig, "파일 C:\\x\\専用.txt 를 확인하세요"))

    def test_path_pattern_linear(self):
        for line in ("/" + "a" * 20000, "/a" * 10000 + " x", "C:\\" + "a\\" * 10000, "~/" + "a/" * 10000,
                     "MEDIA:" + "x" * 20000):
            start = time.perf_counter()
            E.path_spans(line)
            self.assertLess(time.perf_counter() - start, 0.1, line[:20])


class HostNoteSpeed(unittest.TestCase):
    """The host-notice patterns run on every user message: they must stay linear."""

    def _fast(self, text):
        start = time.perf_counter()
        E.strip_host_notes(text)
        return time.perf_counter() - start

    def test_tag_without_backtracking(self):
        line = "[A" + "-A" * 99
        self.assertEqual(len(line), 200)
        for text in (line, line + "] x", line + "]", "안녕\n" + line):
            self.assertLess(self._fast(text), 0.1, text[:30])

    def test_note_bounded(self):
        many = "\n".join(["[Note 메모"] * 2000) + "\n끝"
        self.assertLess(self._fast(many), 0.5)
        self.assertLess(self._fast("[Note " + "a" * 100000), 0.1)

    def test_notices_still_removed(self):
        msg = ("한국어로 답해 줘\n\n[System: The skills available changed.\nNow available:\n"
               "    - korean-only-output: Use for all replies]")
        self.assertEqual(E.strip_host_notes(msg), "한국어로 답해 줘")
        msg = "[OUT-OF-BAND USER MESSAGE — a direct message]\n확실하게 수정해.\n[/OUT-OF-BAND USER MESSAGE]"
        self.assertEqual(E.strip_host_notes(msg), "확실하게 수정해.")

    def test_attached_context_is_not_the_users_words(self):
        # Hermes appends an English note for every @file: attachment; it must not set the language
        msg = "@file:Downloads/【KOREC専用】履歴書_JANG_JOOWON.xlsx\n\n여기에 병역을 추가해줘.\n2018년9월입대 2020년4월전역\n대한민국 육군\n\n--- Attached Context ---\n\n📎 @file:Downloads/【KOREC専用】履歴書_JANG_JOOWON.xlsx (application/vnd.ms-excel, 557.8 KB) — binary file, not inlined as text. It is available on disk at `C:\\\\Users\\\\x\\\\a.xlsx`. Use your tools to work with it (read or convert it, extract its text, or view/render it as needed); do not tell the user the file type is unsupported."
        self.assertEqual(E.strip_host_notes(msg).splitlines()[-1], "대한민국 육군")
        self.assertEqual(E.resolve_turn({}, "s", msg, default="en")[0], "ko")
        warn = "고쳐 줘\n\n--- Context Warnings ---\n- @x: not expanded (maximum 8 @-references per message)"
        self.assertEqual(E.strip_host_notes(warn), "고쳐 줘")
        self.assertEqual(E.strip_host_notes("앞\n--- Attached Contexts ---\n뒤"), "앞\n--- Attached Contexts ---\n뒤")
        self.assertLess(self._fast("\n" * 20000 + "--- Attached Context ---"), 0.1)


class Korean(unittest.TestCase):
    def test_english_leaks(self):
        self.assertEqual(c("weight가 check 대상"), "비중이 확인 대상")
        self.assertEqual(c("check는데 문제없다"), "확인하는데 문제없다")
        self.assertEqual(c("상대가 기한을 gave다."), "상대가 기한을 줬다.")
        self.assertEqual(c("Weight가 중요합니다."), "비중이 중요합니다.")
        self.assertEqual(c("Actually 이건 중요해요."), "실제로 이건 중요해요.")

    def test_hanja(self):
        self.assertEqual(c("正当한 混入, 女子 대학, 列車, 比率, 労働 조건"),
                         "정당한 혼입, 여자 대학, 열차, 비율, 노동 조건")
        self.assertEqual(c("最常见的 결과"), "가장 흔한 결과")

    def test_untouched(self):
        for s in ["Next.js와 Go로 만든 GitHub 저장소", "docker로 띄우고 react로 만든다",
                  "IT 기업, COE 발급, https://a.b/docs, plugin.yaml", "순수 한국어 문장입니다.",
                  "예시는 `weight가` 처럼 코드로", "```\n中小企業 weight가\n```"]:
            self.assertEqual(c(s), s)

    def test_allowed_second_language(self):
        s = "문구: お忙しいところ恐れ入ります"
        self.assertEqual(c(s, "ko", {"ko", "ja"}), s)

    def test_english_phrase(self):
        self.assertIn("⚠", c("보통 one week 정도"))
        self.assertEqual(c("npm install 후 git push 하세요"), "npm install 후 git push 하세요")

    def test_other_script_leak(self):
        self.assertEqual(c("면접은 обычно 두 번"), "면접은 обычно⚠ 두 번")
        unmarked = E.clean("면접은 обычно 두 번", rewrite=False, target="ko", allowed=frozenset({"ko"}))[0]
        self.assertEqual(unmarked, "면접은 обычно 두 번")          # default: left as written


class OtherTargets(unittest.TestCase):
    def test_marks(self):
        self.assertIn("⚠", c("The offer is safe. 다만 the letter.", "en"))
        self.assertIn("⚠", c("這样的 ケース", "zh"))
        self.assertIn("⚠", c("交渉は可能です。这样的", "ja"))
        self.assertNotIn("⚠", c("一般的な交渉です。", "ja"))
        self.assertIn("⚠", c("El proceso dura, however the salary.", "es"))

    def test_cognates_kept(self):
        for s, t in [("Le point important est simple.", "fr"), ("Das ist auch was ich will, also bitte.", "de"),
                     ("Il processo è semplice e veloce.", "it")]:
            self.assertEqual(c(s, t), s)


class Detection(unittest.TestCase):
    CASES = {
        "일본 IT 기업 내정 알려줘": "ko", "How does hiring work in Japan?": "en",
        "日本の内定について教えて": "ja", "请介绍日本的内定流程": "zh",
        "مرحبا، كيف تعمل عملية التوظيف في الشركات اليابانية؟": "ar",
        "سلام، فرایند استخدام در شرکت‌های ژاپنی چگونه است؟": "fa",
        "שלום, איך עובד תהליך הגיוס בחברות יפניות?": "he",
        "สวัสดี กระบวนการรับสมัครงานในบริษัทญี่ปุ่นเป็นอย่างไร": "th",
        "जापानी आईटी कंपनियों में भर्ती प्रक्रिया कैसे काम करती है?": "hi",
        "Як працює процес найму в японських компаніях?": "uk",
        "Как проходит собеседование в японской компании?": "ru",
        "Come funziona il processo di assunzione nelle aziende giapponesi?": "it",
        "Quy trình tuyển dụng ở công ty Nhật như thế nào?": "vi",
        "Wie funktioniert der Bewerbungsprozess in japanischen Firmen?": "de",
    }

    def test_detect(self):
        for text, lang in self.CASES.items():
            self.assertEqual(E.detect_language(text), lang, text)

    def test_commands(self):
        self.assertEqual(R("t1", "앞으로는 영어로 대답해줘")[0], "en")
        self.assertEqual(R("t1", "연봉 협상 팁 알려줘")[0], "en")      # sticky
        self.assertEqual(R("t1", "다시 한국어로 대답해")[0], "ko")
        self.assertEqual(R("t2", "이번에는 영어로 답해줘")[0], "en")
        self.assertEqual(R("t2", "고마워")[0], "ko")                  # one-off
        t, allowed, _ = R("t3", "일본어로 메일 초안 만들어줘")
        self.assertEqual((t, "ja" in allowed), ("ko", True))
        t, allowed, _ = R("t4", "> 内定のご連絡、誠にありがとうございます。\n이 문장 무슨 뜻이야?")
        self.assertEqual((t, "ja" in allowed), ("ko", True))


class Hermes(unittest.TestCase):
    def test_background_turn_untouched(self):
        # a review fork shares the session but fires no pre_llm_call for its turn
        H._on_pre_llm(session_id="s", user_message="질문", turn_id="T1")
        self.assertIsNone(H._on_transform(response_text="English summary with 中小企業", session_id="s",
                                          turn_id="T2"))
        out = H._on_transform(response_text="中小企業 오퍼", session_id="s", turn_id="T1")
        self.assertEqual(out, "중소기업 오퍼")

    def test_register(self):
        seen = {}

        class Ctx:
            llm = None

            def register_auxiliary_task(self, key, **kw):
                seen["task"] = key

            def register_hook(self, name, cb, **kw):
                seen.setdefault("hooks", []).append(name)

            def register_middleware(self, kind, cb):
                seen.setdefault("middleware", []).append(kind)

        H.register(Ctx())
        self.assertEqual(seen["task"], "pure_language_rewrite")
        self.assertEqual(seen["hooks"], ["pre_llm_call", "post_tool_call", "transform_tool_result",
                                         "on_interim_message", "transform_llm_output"])
        self.assertEqual(seen["middleware"], ["llm_request"])

    def test_progress_note_feedback(self):
        H._on_pre_llm(session_id="n", user_message="이 계정을 지워 줘", turn_id="N1")
        first = H._on_tool_result(result="ok", session_id="n", turn_id="N1")
        self.assertIn("[출력 규칙] 진행 설명까지", first)
        H._on_interim(text="Deleting it. If the feature runs again, Windows will recreate it.",
                      session_id="n", turn_id="N1")
        fixed = H._on_tool_result(result="done", session_id="n", turn_id="N1")
        self.assertIn("방금 쓴 진행 설명이 한국어가 아니었다", fixed)
        # a Korean note brings back the normal one-line rule
        H._on_interim(text="계정을 지웠습니다. 남은 폴더를 확인하겠습니다.", session_id="n", turn_id="N1")
        self.assertIn("[출력 규칙] 진행 설명까지", H._on_tool_result(result="x", session_id="n", turn_id="N1"))
        # the next turn starts with the full rule
        guard = H._on_pre_llm(session_id="n", user_message="고마워", turn_id="N2")
        self.assertIn("legally 검토", guard)

    def test_history_note_leak(self):
        history = [{"role": "user", "content": "지워 줘"},
                   {"role": "assistant", "content": "Deleting it now, then checking the folder.",
                    "tool_calls": [{"id": "1"}]},
                   {"role": "tool", "content": "ok"},
                   {"role": "assistant", "content": "지웠습니다."}]
        guard = H._on_pre_llm(session_id="h", user_message="다음은?", turn_id="H1", conversation_history=history)
        self.assertIn("legally 검토", guard)
        self.assertIn("앞선 진행 설명 중", H._on_tool_result(result="ok", session_id="h", turn_id="H1"))
        self.assertIn("[출력 규칙] 진행 설명까지", H._on_tool_result(result="ok", session_id="h", turn_id="H1"))

    def test_request_notes_in_answer_language(self):
        note = "Deleting it now, then I will check the remaining folder."
        fixed = "지금 지우고, 남은 폴더를 확인하겠습니다."
        calls = []
        saved = H._rewriter
        H._rewriter = lambda lines, *a: (calls.append(list(lines)), [fixed] * len(lines))[1]
        try:
            H._on_pre_llm(session_id="m", user_message="이 계정 지워 줘", turn_id="M1")
            chat = {"messages": [{"role": "user", "content": "지워 줘"},
                                 {"role": "assistant", "content": note, "tool_calls": [{"id": "1"}]},
                                 {"role": "tool", "content": "ok"},
                                 {"role": "assistant", "content": "Done. The account is gone and nothing remains."}]}
            out = H._on_llm_request(request=chat, session_id="m", turn_id="M1")
            self.assertEqual(out["request"]["messages"][1]["content"], fixed)
            self.assertNotEqual(out["request"]["messages"][3]["content"], fixed)   # a final answer: not a note
            anth = {"messages": [{"role": "assistant", "content": [{"type": "text", "text": note},
                                                                   {"type": "tool_use", "id": "1"}]}]}
            out = H._on_llm_request(request=anth, session_id="m", turn_id="M1")
            self.assertEqual(out["request"]["messages"][0]["content"][0]["text"], fixed)
            resp = {"input": [{"type": "message", "role": "assistant",
                               "content": [{"type": "output_text", "text": note}]},
                              {"type": "function_call", "name": "x"}]}
            out = H._on_llm_request(request=resp, session_id="m", turn_id="M1")
            self.assertEqual(out["request"]["input"][0]["content"][0]["text"], fixed)
            self.assertEqual(len(calls), 1)                  # cached after the first rewrite
            korean = {"messages": [{"role": "assistant", "content": fixed, "tool_calls": [{"id": "1"}]}]}
            self.assertIsNone(H._on_llm_request(request=korean, session_id="m", turn_id="M1"))
        finally:
            H._rewriter = saved

    def test_held_note_shown_in_answer_language(self):
        """transform_interim_message (held turns): a wrong-language note is rewritten before it is
        shown; a Korean note passes unchanged; one that stays foreign is dropped."""
        saved = H._rewriter
        try:
            H._on_pre_llm(session_id="hn", user_message="이 파일 읽어 줘", turn_id="HN1")
            H._rewriter = lambda lines, *a: ["파일 내용을 읽어 보겠습니다."] * len(lines)
            self.assertEqual(H._on_interim_transform(text="Let me read the file contents now.",
                                                     session_id="hn", turn_id="HN1"),
                             "파일 내용을 읽어 보겠습니다.")
            self.assertIsNone(H._on_interim_transform(text="오류가 나서 메시지를 확인하겠습니다.",
                                                      session_id="hn", turn_id="HN1"))
            H._rewriter = lambda lines, *a: None             # the model call failed
            self.assertEqual(H._on_interim_transform(text="An error occurred; let me check the message.",
                                                     session_id="hn", turn_id="HN1"), "")
            path = "MEDIA:C:\\x\\【KOREC専用】履歴書.xlsx 파일을 만들었습니다."
            self.assertIsNone(H._on_interim_transform(text=path, session_id="hn", turn_id="HN1"))
        finally:
            H._rewriter = saved

    def test_register_held_interim_hook(self):
        seen = []

        class Ctx:
            llm = None

            def register_auxiliary_task(self, key, **kw):
                pass

            def register_hook(self, name, cb, **kw):
                seen.append((name, kw.get("hold_stream", False)))

            def register_middleware(self, kind, cb):
                pass

        H._CFG["hold_stream"] = True
        try:
            H.register(Ctx())
        finally:
            H._CFG._override.pop("hold_stream", None)
        self.assertIn(("transform_llm_output", True), seen)
        self.assertIn(("transform_interim_message", False), seen)

    def test_tool_result_reminder(self):
        H._on_pre_llm(session_id="s", user_message="작업 관리자에서 이 프로세스가 뭔지 알아봐 줘", turn_id="R1")
        out = H._on_tool_result(tool_name="terminal", result='{"output": "Name  Id  CPU"}',
                                session_id="s", turn_id="R1")
        self.assertTrue(out.startswith('{"output": "Name  Id  CPU"}\n\n[출력 규칙]'))
        # the state survives for the final answer
        self.assertIn(("turn", "R1"), H._STATE)
        # a background turn (no pre_llm_call) is left alone
        self.assertIsNone(H._on_tool_result(result="x", session_id="s", turn_id="R2"))

    def test_tool_result_reminder_english(self):
        H._on_pre_llm(session_id="e", user_message="please explain what this process does", turn_id="R3")
        self.assertIsNone(H._on_tool_result(result="plain English output", session_id="e", turn_id="R3"))
        self.assertIn("[Output rule]", H._on_tool_result(result="결과: 정상", session_id="e", turn_id="R3"))


class Structure(unittest.TestCase):
    def test_structure_guard(self):
        self.assertTrue(E._structure_ok("- `x` 와 weight", "- `x` 와 비중"))
        self.assertFalse(E._structure_ok("- `x` 와 weight", "`x` 와 비중"))
        self.assertFalse(E._structure_ok("| a | weight |", "a 비중"))
        self.assertFalse(E._structure_ok("see `code`", "see code"))


PASTE = ("PS C:\\Windows\\System32> & 'C:\\Program Files\\WindowsApps\\cdb.exe' -z C:\\dump.dmp "
         "-c \"lm m vgk*; q\" | Select-String vgk\n>>\n\n11: kd> cdb: Reading initial command 'lm m vgk*; q'\n\n"
         "이렇게 명령어 결과를 보여준 것처럼 맥락에 따라 다른 언어가 필요할 수 있어. 수정 가능할까?")
TOOL = ("11: kd> cdb: Reading initial command 'lm m vgk*; q'\nAccess is denied.\n"
        "fffff806`1a2b0000 fffff806`1a5c0000   vgk        (deferred)\n中小企業の採用ページです")


class TechnicalContext(unittest.TestCase):
    def test_pasted_terminal_is_not_a_language(self):
        target, allowed, _ = E.resolve_turn({}, "p", PASTE, default="en")
        self.assertEqual(target, "ko")
        self.assertEqual(allowed, frozenset({"ko"}))      # was misread as Polish

    def test_technical_lines(self):
        for l in ["PS C:\\> dir", "$ npm install", "11: kd> !analyze -v", ">>> import os",
                  "Traceback (most recent call last):", '  File "a.py", line 3, in <module>',
                  "0x20001 Arg1=0x28 (I/O MMU)", "2026-11-25 17:55:01 ERROR boot failed",
                  "joowon@box:~/src$ make"]:
            self.assertTrue(E.is_technical(l), l)
        for l in ["보통 일주일 걸립니다.", "Open the file and restart it.", "- `config.yaml`의 설정",
                  "| 도구 | 설치 |", "> 인용문입니다", "## 요약"]:
            self.assertFalse(E.is_technical(l), l)

    def test_quoted_output_kept_leaks_still_caught(self):
        ans = ("11: kd> cdb: Reading initial command 'lm m vgk*; q'\n"
               "결과에 \"Reading initial command 'lm m vgk*; q'\"만 있고 모듈 줄이 없습니다.\n"
               "권한 오류로 Access is denied. 라는 메시지가 보였습니다.\n"
               "그 페이지는 中小企業 대상이고 보통 one week 걸립니다.")
        out, hits = E.clean(ans, set(), set(), rewrite=False, target="ko", allowed={"ko"},
                            user_text="실행해줘", context=TOOL, mark=True)
        lines = out.split("\n")
        self.assertEqual(lines[:3], ans.split("\n")[:3])
        self.assertIn("중소기업", lines[3])            # a short word from the source is still a leak
        self.assertIn("one week⚠", lines[3])

    def test_without_context_error_text_is_a_leak(self):
        _, hits = E.clean("권한 오류로 Access is denied 라는 메시지가 보였습니다.", set(), set(),
                          rewrite=False, target="ko", allowed={"ko"})
        self.assertTrue(hits)

    def test_hermes_tool_output_exempt(self):
        H._on_pre_llm(session_id="t", user_message="덤프 확인해줘", turn_id="Q1")
        H._on_post_tool(tool_name="terminal", result=TOOL, session_id="t", turn_id="Q1")
        ans = "출력은 \"Reading initial command 'lm m vgk*; q'\" 한 줄뿐입니다."
        self.assertIsNone(H._on_transform(response_text=ans, session_id="t", turn_id="Q1"))


WPR = ("PS C:\\Windows\\System32> wpr -status\r\n\r\nMicrosoft Windows Performance Recorder Version 10.0.26100 "
       "(CoreSystem)\r\nCopyright (c) 2024 Microsoft Corporation. All rights reserved.\r\n\r\n"
       "WPR recording is in progress...\r\n\r\nTime since start        : 00:00:15\r\nDropped event           : 0")
EN_ANSWER = ("Tracing is running, in memory mode with no dropped events.\n\n"
             "- **It starts on its own at boot.** `C:\\ProgramData\\trace.log` shows a successful start at 22:35.\n"
             "- **Use the computer as usual.** If a GPU timeout happens again, just tell me.\n\n"
             "```powershell\n& 'C:\\scratch\\wpr_remove.ps1'\n```\n\n"
             "The most important item still open is changing your passwords.")


class PastedOutputOnly(unittest.TestCase):
    def test_output_only_message_keeps_language(self):
        memo = {}
        E.resolve_turn(memo, "w", "추적 설정 확인해줘", default="en")
        target, allowed, _ = E.resolve_turn(memo, "w", WPR, default="en")
        self.assertEqual((target, allowed), ("ko", frozenset({"ko"})))

    def test_prompt_block_then_question(self):
        target, _, _ = E.resolve_turn({}, "q", "PS C:\\> dir\nVolume in drive C has no label\n\nWhy does this fail?",
                                      default="ko")
        self.assertEqual(target, "en")


class WholeAnswer(unittest.TestCase):
    def test_detects_english_answer(self):
        self.assertEqual(E.wrong_language(EN_ANSWER, "ko", {"ko"}), "en")
        ok = ("추적은 잘 돌고 있습니다(`Dropped event : 0`).\n\n```\nWPR recording is in progress...\n"
              "Time since start : 00:00:15\n```\n- `C:\\trace.log`를 보면 정상입니다.")
        self.assertIsNone(E.wrong_language(ok, "ko", {"ko"}))
        self.assertIsNone(E.wrong_language(EN_ANSWER, "en", {"en"}))
        self.assertIsNone(E.wrong_language(EN_ANSWER, "ko", {"ko", "en"}))

    def test_rewrites_whole_answer_keeping_code(self):
        def fake(lines, target, allowed, sample):
            return [E._PREFIX_RE.match(l).group(0) + "한국어 문장 " + " ".join(E._INLINE_CODE.findall(l))
                    for l in lines]
        out, hits = E.clean(EN_ANSWER, target="ko", allowed={"ko"}, rewriter=fake)
        self.assertNotIn("Tracing", out)
        self.assertIn("- 한국어 문장 `C:\\ProgramData\\trace.log`", out)
        self.assertIn("& 'C:\\scratch\\wpr_remove.ps1'", out)
        self.assertEqual(hits[0][:2], ("whole-rewrite", "en"))

    def test_failed_rewrite_leaves_answer_untouched(self):
        def boom(*a):
            raise TimeoutError
        out, _ = E.clean(EN_ANSWER, target="ko", allowed={"ko"}, rewriter=boom)
        self.assertEqual(out, EN_ANSWER)          # no ⚠ sprinkled over every word
        self.assertEqual(E.leaks(EN_ANSWER, target="ko", allowed={"ko"})[0][0], "whole-mark")


class Cost(unittest.TestCase):
    def test_short_guard_by_default(self):
        for lang in ("ko", "en", "ja", "zh", "fr", "th"):
            self.assertLess(len(E.guard_for(lang, frozenset({lang}))), 200, lang)
            self.assertGreater(len(E.guard_for(lang, frozenset({lang}), full=True)),
                               len(E.guard_for(lang, frozenset({lang}))))

    def test_hermes_full_guard_only_after_leak(self):
        g1 = H._on_pre_llm(session_id="g", user_message="연봉 협상 팁 알려줘", turn_id="G1")
        self.assertLess(len(g1), 110)
        H._on_transform(response_text="보통 one week 걸리고 中小企業 기준입니다.", session_id="g", turn_id="G1")
        g2 = H._on_pre_llm(session_id="g", user_message="그럼 신입은?", turn_id="G2")
        self.assertGreater(len(g2), 200)
        H._on_transform(response_text="신입은 보통 두 번입니다.", session_id="g", turn_id="G2")
        g3 = H._on_pre_llm(session_id="g", user_message="경력은?", turn_id="G3")
        self.assertLess(len(g3), 110)

    def test_only_leaking_sentences_are_sent(self):
        sent = []

        def fake(lines, target, allowed, sample):
            sent.extend(lines)
            return [l.replace("tricky한", "까다로운") for l in lines]
        line = ("설정 파일은 기본 폴더에 있습니다. 이 설정은 꽤 tricky한 편이라 주의해야 합니다. "
                "변경 후에는 다시 시작해야 합니다. 나머지는 기본값으로 두면 됩니다.")
        out, _ = E.clean(line, target="ko", allowed={"ko"}, rewriter=fake)
        self.assertEqual(sent, ["이 설정은 꽤 tricky한 편이라 주의해야 합니다."])
        self.assertEqual(out, line.replace("tricky한", "까다로운"))

    def test_rewrite_budget(self):
        self.assertLess(len(E.rewrite_instructions("ko", frozenset({"ko"}), "")), 260)
        self.assertEqual(E.rewrite_max_tokens(["가" * 10]), 84)


class WovenIn(unittest.TestCase):
    """Allowed languages may be quoted, not woven into Korean sentences."""
    JA = frozenset({"ko", "ja"})

    def c(self, text, allowed):
        return E.clean(text, set(), set(), rewrite=False, target="ko", allowed=allowed, mark=True)

    def test_gloss(self):
        self.assertEqual(self.c("일본의 内定(내정) 취소", self.JA)[0], "일본의 내정(`内定`) 취소")
        self.assertEqual(self.c("일본의 内定(내정) 취소", frozenset({"ko"}))[0], "일본의 내정 취소")

    def test_glued_run_is_a_leak_even_if_allowed(self):
        out, hits = self.c("다릅니다. 日本の中途採用에서는 서명을 요구합니다.", self.JA)
        self.assertNotIn("中途採用", out)
        self.assertTrue(hits)

    def test_chinese_grammar_with_only_japanese_allowed(self):
        out, hits = self.c("1. **취소理由是 서면으로 요구합니다.**", self.JA)
        self.assertIn("是⚠", out)

    def test_quotes_stay(self):
        line = "판례는 `始期付解約権留保付労働契約`으로 봅니다. 「お忙しいところ」 같은 표현"
        self.assertEqual(self.c(line, self.JA), (line.replace("「", '"').replace("」", '"'), []))

    def test_standalone_prose_word(self):
        out, hits = self.c(" legally 다음을 순서대로 검토할 수 있습니다.", frozenset({"ko"}))
        self.assertIn("legally⚠", out)
        for ok in ("msiexec 인자 오류로 실패했습니다.", "npm install 후 git push 하세요", "cron 작업이 돌았습니다"):
            self.assertEqual(self.c(ok, frozenset({"ko"}))[0], ok)


class Guard(unittest.TestCase):
    def test_asks_for_korean_terms_not_bare_originals(self):
        g = E.guard_for("ko", frozenset({"ko"}))
        self.assertIn("한국어로 옮기", g)
        self.assertIn("내정 취소(`内定取消し`)", g)
        self.assertNotIn("원문은 백틱 안에 쓴다", g)   # that wording produced answers full of bare Japanese terms

    def test_pasted_languages_are_not_advertised(self):
        msg = ("참고용으로 붙여줄게.\nI now have thorough information. Let me compose the answer.\n"
               "内定は始期付解約権留保付労働契約が成立したものとされる。\n\n이런 식이야")
        g = H._on_pre_llm(session_id="pa", user_message=msg, turn_id="PA1")
        self.assertNotIn("허용", g)
        self.assertIn("ja", H._STATE[("turn", "PA1")]["allowed"])   # still tolerated as quotes
        g = H._on_pre_llm(session_id="pb", user_message="일본어로 메일 초안 써줘", turn_id="PB1")
        self.assertIn("일본어", g)


class Deadline(unittest.TestCase):
    def setUp(self):
        H._BREAKER["until"] = 0.0
        self.saved = (H._CTX, H._call_model, H._CFG["timeout"])

    def tearDown(self):
        H._CTX, H._call_model, H._CFG["timeout"] = self.saved
        H._BREAKER["until"] = 0.0

    def test_slow_model_falls_back_in_time_and_pauses(self):
        import time as _t

        class Ctx:
            llm = object()
        H._CTX = Ctx()
        H._CFG["timeout"] = 5
        H._call_model = lambda *a: (_t.sleep(8), ["x"])[1]
        H._on_pre_llm(session_id="d", user_message="설정 알려줘", turn_id="D1")
        t0 = _t.time()
        out = H._on_transform(response_text="반영까지 보통 one week 정도 걸립니다.", session_id="d", turn_id="D1")
        self.assertLess(_t.time() - t0, 7)               # well under Hermes' 30 s hook limit
        self.assertIsNone(out)                            # unfixed words are left as written
        self.assertGreater(H._BREAKER["until"], _t.time())
        H._on_pre_llm(session_id="d", user_message="또?", turn_id="D2")
        t0 = _t.time()
        H._on_transform(response_text="이것도 tricky한 부분입니다.", session_id="d", turn_id="D2")
        self.assertLess(_t.time() - t0, 1)               # paused: no second wait


class CliHooks(unittest.TestCase):
    """hooks/run.py as Claude Code and Codex call it (stdin JSON -> stdout JSON)."""

    def run_hook(self, event, payload, env_extra):
        env = dict(os.environ, **env_extra)
        p = subprocess.run([sys.executable, os.path.join(ROOT, "hooks", "run.py"), event],
                           input=json.dumps(payload).encode("utf-8"), capture_output=True, env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        out = p.stdout.decode("utf-8").strip()
        return json.loads(out) if out else None

    def setUp(self):
        self.data = tempfile.mkdtemp(prefix="pl-data-")
        self.home = tempfile.mkdtemp(prefix="pl-home-")
        with open(os.path.join(self.home, "pure-language.json"), "w", encoding="utf-8") as fh:
            json.dump({"default_language": "ko"}, fh)

    def test_claude(self):
        env = {"CLAUDE_PLUGIN_DATA": self.data, "CLAUDE_CONFIG_DIR": self.home}
        out = self.run_hook("prompt", {"session_id": "c1", "prompt": "면접은 몇 번 하나요?"}, env)
        self.assertIn("한국어로 쓴다", out["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(self.run_hook("stop", {"session_id": "c1", "stop_hook_active": False,
                                                 "last_assistant_message": "보통 두 번입니다."}, env))
        out = self.run_hook("stop", {"session_id": "c1", "stop_hook_active": False,
                                     "last_assistant_message": "보통 one week 걸리고 中小企業 기준이며 tricky한 부분과 salary 협의가 있습니다."}, env)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Korean", ctx)
        self.assertIn("one week", ctx)
        self.assertIsNone(self.run_hook("stop", {"session_id": "c1", "stop_hook_active": True,
                                                 "last_assistant_message": "one week"}, env))

    def test_codex(self):
        env = {"PLUGIN_DATA": self.data, "CODEX_HOME": self.home, "PLUGIN_ROOT": ROOT}
        out = self.run_hook("prompt", {"session_id": "x1", "turn_id": "t1",
                                       "prompt": "How does hiring work in Japan?"}, env)
        self.assertIn("English", out["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.run_hook("stop", {"session_id": "x1", "turn_id": "t1", "stop_hook_active": False,
                                                "last_assistant_message": "It takes two weeks."}, env), {})
        # one stray word: no extra turn, but the next turn gets the full rule
        out = self.run_hook("stop", {"session_id": "x1", "turn_id": "t1", "stop_hook_active": False,
                                                "last_assistant_message": "It takes 보통 two weeks."}, env)
        self.assertEqual(out["decision"], "block")   # one stray word is enough now
        self.assertIn("보통", out["reason"])
        out = self.run_hook("prompt", {"session_id": "x1", "turn_id": "t2", "prompt": "And for juniors?"}, env)
        self.assertIn("Do not mix Korean", out["hookSpecificOutput"]["additionalContext"])
        out = self.run_hook("prompt", {"session_id": "x1", "turn_id": "t3", "prompt": "And for seniors?"}, env)
        self.assertNotIn("Do not mix Korean", out["hookSpecificOutput"]["additionalContext"])
        # several leaked words: ask for a correction
        out = self.run_hook("stop", {"session_id": "x1", "turn_id": "t3", "stop_hook_active": False,
                                     "last_assistant_message": "It takes 보통 two weeks, 대략 그 정도 for 신입."}, env)
        self.assertEqual(out["decision"], "block")
        self.assertIn("보통", out["reason"])

    def test_claude_transcript_tool_output(self):
        env = {"CLAUDE_PLUGIN_DATA": self.data, "CLAUDE_CONFIG_DIR": self.home}
        tr = os.path.join(self.data, "t.jsonl")
        with open(tr, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "user", "message": {"content": "덤프 결과 보여줘"}}, ensure_ascii=False) + "\n")
            fh.write(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result",
                     "content": "Access is denied by the kernel debugger."}]}}) + "\n")
        self.run_hook("prompt", {"session_id": "c3", "prompt": "덤프 결과 보여줘"}, env)
        out = self.run_hook("stop", {"session_id": "c3", "stop_hook_active": False, "transcript_path": tr,
                                     "last_assistant_message": "결과는 \"Access is denied by the kernel debugger.\" 입니다."}, env)
        self.assertIsNone(out)

    def test_claude_whole_answer_feedback(self):
        env = {"CLAUDE_PLUGIN_DATA": self.data, "CLAUDE_CONFIG_DIR": self.home}
        self.run_hook("prompt", {"session_id": "c4", "prompt": "추적 상태 확인해줘"}, env)
        self.run_hook("prompt", {"session_id": "c4", "prompt": WPR}, env)
        out = self.run_hook("stop", {"session_id": "c4", "stop_hook_active": False,
                                     "last_assistant_message": EN_ANSWER}, env)
        self.assertIn("written in English", out["hookSpecificOutput"]["additionalContext"])

    def test_sticky_across_processes(self):
        env = {"CLAUDE_PLUGIN_DATA": self.data, "CLAUDE_CONFIG_DIR": self.home}
        self.run_hook("prompt", {"session_id": "c2", "prompt": "앞으로는 영어로 대답해줘"}, env)
        out = self.run_hook("prompt", {"session_id": "c2", "prompt": "연봉 협상 팁 알려줘"}, env)
        self.assertIn("English", out["hookSpecificOutput"]["additionalContext"])


class SentenceEndWords(unittest.TestCase):
    def test_word_before_period_is_checked(self):
        text, hits = E.clean("결과를 확인했습니다 legally.", set(), set(), rewrite=False, target="ko")
        self.assertIn("legally", [h[1] for h in hits])

    def test_file_names_still_skipped(self):
        text, hits = E.clean("설정은 legally.txt 파일과 notes.md에 있습니다.", set(), set(), rewrite=False, target="ko")
        self.assertEqual(hits, [])


class ModelFirst(unittest.TestCase):
    """With a rewriter every leaking line goes to the model; rules are the fallback."""

    def test_fixable_word_goes_to_model(self):
        seen = []
        def rw(lines, *a):
            seen.extend(lines)
            return ["보통 서류 전형을 거칩니다."]
        text, hits = E.clean("보통 書類選考를 거칩니다.", set(), set(), target="ko", rewriter=rw)
        self.assertEqual(seen, ["보통 書類選考를 거칩니다."])
        self.assertEqual(text, "보통 서류 전형을 거칩니다.")

    def test_rules_are_fallback(self):
        def rw(lines, *a):
            raise RuntimeError("offline")
        text, _ = E.clean("오늘 schedule을 정했습니다.", set(), set(), target="ko", rewriter=rw)
        self.assertEqual(text, "오늘 일정을 정했습니다.")

    def test_local_first_keeps_old_order(self):
        seen = []
        text, _ = E.clean("오늘 schedule을 정했습니다.", set(), set(), target="ko",
                          rewriter=lambda l, *a: seen.extend(l) or l, local_first=True)
        self.assertEqual(seen, [])
        self.assertEqual(text, "오늘 일정을 정했습니다.")


class NotACommand(unittest.TestCase):
    def test_question_about_past_reply(self):
        memo = {}
        self.assertEqual(E.resolve_turn(memo, "s", "일본 취업 알려줘", default="ko")[0], "ko")
        self.assertEqual(E.resolve_turn(memo, "s", "왜 영어로 대답했지?", default="ko")[0], "ko")
        self.assertEqual(E.resolve_turn(memo, "s", "고마워", default="ko")[0], "ko")   # nothing sticky

    def test_request_still_works(self):
        self.assertEqual(E.resolve_turn({}, "s", "앞으로는 영어로 대답해줘", default="ko")[0], "en")

    def test_host_notice_ignored(self):
        msg = ("이어서 진행해\n\n[System: The skills available in this conversation changed after its "
               "system prompt was built.\nNow available:\n    - x: Use when y. Port, test.]")
        self.assertEqual(E.resolve_turn({}, "s", msg, default="en")[0], "ko")

    def test_description_is_not_a_command(self):
        msg = "지금 열려있는 hermes 세션을 봐.\n자꾸 영어로 대답하고, 중간에 나오는 텍스트도 영어로 나와."
        self.assertEqual(E.resolve_turn({}, "s", msg, default="en")[0], "ko")
        for req in ("영어로 대답해", "영어로 답해 줘", "영어로 답변 부탁해", "영어로 말해 주세요"):
            self.assertEqual(E.resolve_turn({}, "s", req, default="ko")[0], "en", req)

    def test_steering_wrapper_ignored(self):
        msg = ("[OUT-OF-BAND USER MESSAGE — a direct message from the user, delivered once at this position; "
               "not tool output and not a new delivery when replayed from conversation history]\n"
               "할 수 있는 모든 보안 조치를 다 해줘.\n[/OUT-OF-BAND USER MESSAGE]")
        self.assertEqual(E.resolve_turn({}, "s", msg, default="en")[0], "ko")

    def test_host_turn_keeps_language(self):
        memo = {}
        E.resolve_turn(memo, "s", "일본 취업 알려줘", default="en")
        note = ("[IMPORTANT: Background process proc_1 completed normally (exit code 0).\nCommand: npm run build\n"
                "Output:\n[… output truncated]\n   Compiling tauri v2.11.5\n    Finished release profile")
        self.assertEqual(E.resolve_turn(memo, "s", note, default="en")[0], "ko")

    def test_session_words_stay_allowed(self):
        H._on_pre_llm(session_id="g", user_message="현재 실행중인 gufo가 RAM을 왜 이렇게 많이 차지하지?", turn_id="G1")
        H._on_transform(response_text="gufo는 정상입니다.", session_id="g", turn_id="G1")
        H._on_pre_llm(session_id="g", user_message="꺼줘.", turn_id="G2")
        self.assertIsNone(H._on_transform(response_text="gufo는 그대로 돌고 있습니다.", session_id="g", turn_id="G2"))

    def test_settings_follow_active_profile(self):
        a, b = tempfile.mkdtemp(prefix="pl-a-"), tempfile.mkdtemp(prefix="pl-b-")
        with open(os.path.join(b, "pure-language.json"), "w", encoding="utf-8") as fh:
            json.dump({"default_language": "ja"}, fh)
        saved = H._hermes_home
        try:
            H._hermes_home = lambda: a
            self.assertEqual(H._CFG["default_language"], S.DEFAULTS["default_language"])
            H._hermes_home = lambda: b
            self.assertEqual(H._CFG["default_language"], "ja")
        finally:
            H._hermes_home = saved

    def test_short_english_word_keeps_language(self):
        memo = {}
        E.resolve_turn(memo, "s", "한국어로 하라고", default="en")
        for word in ("continue", "ok", "go on", "yes please"):
            self.assertEqual(E.resolve_turn(memo, "s", word, default="en")[0], "ko", word)
        self.assertEqual(E.resolve_turn({}, "s", "continue", default="ko")[0], "ko")   # fresh process
        self.assertEqual(E.resolve_turn(memo, "s", "answer in English", default="ko")[0], "en")

    def test_own_rule_line_ignored(self):
        msg = "계속\n\n[Output rule] Write this entire answer in English only."
        self.assertEqual(E.resolve_turn({}, "s", msg, default="en")[0], "ko")


if __name__ == "__main__":
    unittest.main(verbosity=1)
