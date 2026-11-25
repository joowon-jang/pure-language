"""Regression tests for pure-language (standard library only, no model calls).

    python -m unittest discover -s tests        # from the repository root
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["HERMES_HOME"] = tempfile.mkdtemp(prefix="pl-hermes-")   # isolated settings and logs

from engine import core as E  # noqa: E402

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
    return E.clean(text, rewrite=False, target=target, allowed=frozenset(allowed or {target}))[0]


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
        self.assertEqual(c("면접은 обычно 두 번"), "면접은 ⚠ 두 번")


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

        H.register(Ctx())
        self.assertEqual(seen["task"], "pure_language_rewrite")
        self.assertEqual(seen["hooks"], ["pre_llm_call", "transform_llm_output"])


class Structure(unittest.TestCase):
    def test_structure_guard(self):
        self.assertTrue(E._structure_ok("- `x` 와 weight", "- `x` 와 비중"))
        self.assertFalse(E._structure_ok("- `x` 와 weight", "`x` 와 비중"))
        self.assertFalse(E._structure_ok("| a | weight |", "a 비중"))
        self.assertFalse(E._structure_ok("see `code`", "see code"))


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
        self.assertIn("한글", out["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(self.run_hook("stop", {"session_id": "c1", "stop_hook_active": False,
                                                 "last_assistant_message": "보통 두 번입니다."}, env))
        out = self.run_hook("stop", {"session_id": "c1", "stop_hook_active": False,
                                     "last_assistant_message": "보통 one week 걸리고 中小企業 기준입니다."}, env)
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
        out = self.run_hook("stop", {"session_id": "x1", "turn_id": "t1", "stop_hook_active": False,
                                     "last_assistant_message": "It takes 보통 two weeks."}, env)
        self.assertEqual(out["decision"], "block")
        self.assertIn("보통", out["reason"])

    def test_sticky_across_processes(self):
        env = {"CLAUDE_PLUGIN_DATA": self.data, "CLAUDE_CONFIG_DIR": self.home}
        self.run_hook("prompt", {"session_id": "c2", "prompt": "앞으로는 영어로 대답해줘"}, env)
        out = self.run_hook("prompt", {"session_id": "c2", "prompt": "연봉 협상 팁 알려줘"}, env)
        self.assertIn("English", out["hookSpecificOutput"]["additionalContext"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
