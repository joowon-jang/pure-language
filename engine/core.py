"""pure-language engine: language detection and mixed-language leak cleaning.

Pure standard-library Python, shared by every adapter (Hermes plugin, Claude
Code and Codex hooks). Nothing in here talks to a model or a host: the one
model-dependent step, rewriting lines the local rules cannot fix, is passed in
as a ``rewriter`` callable by the adapter.

Public API
  detect_language(text) -> code | None
  resolve_turn(memo, session_key, message, default="en") -> (target, allowed, skip)
  guard_for(target, allowed) -> str
  clean(text, ..., rewriter=None) -> (cleaned_text, hits)
  leaks(text, ...) -> hits that would be reported (no rewriting)
  rewrite_instructions(target, allowed, sample) / REWRITE_SCHEMA
  write_log(path, session_id, hits)
"""

import json
import logging
import os
import re
import time

log = logging.getLogger(__name__)

_REWRITE_CACHE = {}         # (target, allowed, line) -> rewritten line
_CACHE_MAX = 500

_GUARD = (
    "[출력 규칙] 진행 설명까지 모두 한국어로 쓴다. 외국어 용어는 반드시 한국어로 옮겨 쓴다. "
    "원문이 꼭 필요하면 처음 한 번만 한국어 뒤 괄호 안 백틱으로 덧붙인다. 예: 내정 취소(`内定取消し`). "
    "원문을 한국어 대신 쓰거나, 한국어 문장에 외국어 단어를 붙여 쓰지 않는다(`日本の採用에서는`, `legally 검토`처럼 쓰지 않는다). "
    "외국어 자료를 읽었거나 붙여넣은 출력이 외국어여도 답변 언어는 바꾸지 않는다. "
    "코드, 명령, 경로, 고유명사, 약어는 그대로 둔다."
)

# ------------------------------------------------------------------ data
_HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
try:
    with open(os.path.join(_HERE, "hanja_ko.json"), encoding="utf-8") as _fh:
        _READ = json.load(_fh)
except (OSError, ValueError) as _exc:  # plugin still works, just without readings
    log.warning("pure-language: hanja table unavailable: %s", _exc)
    _READ = {}

_CJK_RE = re.compile("[\u3040-\u30FF\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]+")
_KANA_RE = re.compile("[\u3040-\u30FF]")
# Chinese grammar/colloquial characters: a char-by-char reading would be nonsense
_ZH_FUNC = set("的了吗吧呢啊么们这那哪是在和与就都也还很没不被把给让从对着过得地个些吗嘛呀啥咱您俺它她他我你")
_PUNCT_MAP = {'、': ', ', '。': '. ', '「': '"', '」': '"', '『': '"', '』': '"', '〈': '<', '〉': '>', '【': '[', '】': ']', '・': '·', '々': '', '！': '!', '？': '?', '：': ':', '；': ';', '，': ',', '（': '(', '）': ')', '％': '%', '＃': '#', '＄': '$', '＊': '*', '－': '-', '／': '/', '＼': '\\', '＾': '^', '｀': '`', '｛': '{', '｝': '}', '～': '~', '〜': '~'}
_WORDS = {'中小企业': '중소기업', '中小企業': '중소기업', '企業': '기업', '比价': '비교', '比價': '비교', '会社': '회사', '内定': '내정', '先日': '전날', '検讨': '검토', '检讨': '검토', '検討': '검토', '面接': '면접', '面談': '면담', '面谈': '면담', '待遇': '대우', '提示': '제시', '条件': '조건', '連絡': '연락', '联络': '연락', '確認': '확인', '确认': '확인', '返信': '회신', '回复': '회신', '答复': '답변', '回答': '답변', '入社': '입사', '入职': '입사', '退社': '퇴사', '离职': '퇴사', '退职': '퇴직', '辞职': '사직', '代表': '대표', '会计': '회계', '会計': '회계', '経理': '회계', '人事': '인사', '经验': '경험', '經驗': '경험', '实绩': '실적', '実績': '실적', '实际': '실제', '実際': '실제', '手续': '수속', '手続': '절차', '决定': '결정', '协定': '합의', '协商': '협의', '協商': '협의', '谈判': '협상', '談判': '협상', '要求': '요구', '说明': '설명', '説明': '설명', '明确': '명확히', '明確': '명확히', '印象': '인상', '意思': '의사', '现在': '현재', '現在': '현재', '时间': '시간', '時間': '시간', '一般': '일반', '通常': '통상', '可能': '가능', '有效': '유효', '问题': '문제', '問題': '문제', '日本': '일본', '韩国': '한국', '韓國': '한국', '薪资': '급여', '工資': '급여', '薪酬': '보수', '基本': '기본', '正当': '정당', '混入': '혼입', '幅': '폭', '尽早': '조속히', '最常见的': '가장 흔한', '特有': '특유', '試用期間': '수습 기간', '试用期': '수습 기간', '賞与': '상여', '奖金': '상여', '残業': '잔업', '加班': '초과 근무', '履歴書': '이력서', '職務経歴書': '경력 기술서', '求人票': '구인 공고', '書類選考': '서류 전형', '内定通知書': '내정 통지서', '内定取消': '내정 취소', '内定辞退': '내정 사퇴', '健康診断書': '건강 진단서', '経験': '경험', '給与': '급여', '年収': '연봉', '相談': '상담', '交渉': '협상', '調整': '조정', '期間': '기간', '契約': '계약', '労働': '노동', '雇用': '고용', '採用': '채용', '中途': '경력', '新卒': '신입', '一般的': '일반적', '様': '님'}
_WORD_RE = re.compile("|".join(re.escape(k) for k in sorted(_WORDS, key=len, reverse=True)))

# a period ends the word only at a sentence end ("legally."), not in "file.txt"
_LATIN_RE = re.compile(r"(?<![A-Za-z0-9_./\\:@#%=&?+~-])([A-Za-z]{2,})(?![A-Za-z0-9_/\\:@#%=&?+~-]|\.[A-Za-z0-9_])")
_HANGUL_CH = re.compile("[\uAC00-\uD7A3]")
_LATIN_ALLOW = {
    "git", "npm", "npx", "pnpm", "yarn", "pip", "uv", "python", "node", "deno", "bun",
    "json", "yaml", "toml", "csv", "xml", "http", "https", "url", "uri", "api", "sdk",
    "cli", "gui", "ui", "ux", "css", "scss", "html", "js", "ts", "jsx", "tsx", "sql",
    "db", "id", "pc", "ok", "vs", "kg", "km", "cm", "mm", "ms", "px", "kb", "mb", "gb",
    "app", "web", "docker", "kubernetes", "react", "vue", "svelte", "nextjs", "vite",
    "webpack", "eslint", "github", "gitlab", "linux", "windows", "macos", "ios",
    "android", "chrome", "vscode", "hermes", "dodo", "agy", "orca", "discord", "gmail",
    "slack", "pdf", "md", "py", "exe", "dll", "ssh", "ip", "dns", "ssl", "tls", "jwt",
    "oauth", "cpu", "gpu", "ram", "ssd", "usb", "bios", "os", "aws", "gcp", "azure",
}
# Capitalized words that are also product / language names
_PROPER_COLLIDE = {"go", "swift", "rust", "ruby", "apple", "notion", "next", "line", "office",
                   "word", "excel", "teams", "edge", "safari", "opera", "spring", "flutter",
                   "express", "unity", "figma", "jira", "zoom", "amazon", "target", "visa"}
_LATIN_WORDS = {'weight': '비중', 'gave': '줬', 'give': '주', 'log': '기록', 'logs': '기록', 'check': '확인', 'update': '갱신', 'issue': '문제', 'point': '요점', 'focus': '초점', 'risk': '위험', 'case': '경우', 'level': '수준', 'range': '범위', 'base': '기준', 'target': '목표', 'option': '선택지', 'timing': '시점', 'deadline': '기한', 'schedule': '일정', 'process': '절차', 'step': '단계', 'detail': '세부', 'details': '세부 사항', 'result': '결과', 'impact': '영향', 'value': '가치', 'salary': '연봉', 'bonus': '상여', 'offer': '제안', 'negotiation': '협상', 'signal': '신호', 'tip': '요령', 'however': '다만', 'because': '때문에', 'actually': '실제로', 'basically': '기본적으로', 'maybe': '아마', 'really': '정말', 'very': '매우', 'also': '또한', 'just': '그냥', 'only': '오직', 'first': '먼저', 'then': '그다음', 'next': '다음', 'finally': '마지막으로', 'good': '좋은', 'bad': '나쁜', 'important': '중요한', 'simple': '단순한', 'safe': '안전한', 'fast': '빠른', 'slow': '느린', 'easy': '쉬운', 'coming': '도착', 'pending': '대기 중', 'done': '완료', 'fix': '수정'}
_LATIN_VERB_STEMS = {"gave", "give", "fix"}   # replacements that already act as verb stems
_LATIN_COMMON = set(_LATIN_WORDS) | set(['a', 'about', 'after', 'again', 'all', 'already', 'always', 'an', 'and', 'any', 'are', 'as', 'at', 'be', 'been', 'before', 'big', 'boss', 'but', 'by', 'can', 'company', 'could', 'did', 'do', 'does', 'each', 'even', 'every', 'few', 'for', 'from', 'get', 'go', 'got', 'had', 'has', 'have', 'he', 'her', 'here', 'high', 'his', 'how', 'if', 'in', 'into', 'is', 'it', 'its', 'job', 'know', 'later', 'less', 'likely', 'long', 'low', 'made', 'make', 'many', 'may', 'me', 'might', 'money', 'more', 'most', 'much', 'must', 'my', 'need', 'never', 'new', 'no', 'not', 'now', 'of', 'often', 'old', 'on', 'or', 'other', 'our', 'over', 'own', 'pay', 'people', 'probably', 'right', 'same', 'see', 'she', 'short', 'should', 'small', 'so', 'some', 'soon', 'still', 'such', 'sure', 'take', 'team', 'than', 'that', 'the', 'them', 'there', 'these', 'they', 'thing', 'things', 'think', 'this', 'those', 'time', 'to', 'under', 'use', 'used', 'usually', 'want', 'was', 'way', 'we', 'well', 'went', 'were', 'what', 'when', 'which', 'while', 'who', 'why', 'will', 'with', 'work', 'would', 'wrong', 'yes', 'you', 'your'])

_PARTICLE_RE = re.compile(r"^(으로|이랑|이|가|은|는|을|를|과|와|로|랑)(?=$|[\s.,!?;:)\]\"'~…·*_])")
_PAIR = {"이": ("이", "가"), "가": ("이", "가"), "은": ("은", "는"), "는": ("은", "는"),
         "을": ("을", "를"), "를": ("을", "를"), "과": ("과", "와"), "와": ("과", "와"),
         "으로": ("으로", "로"), "로": ("으로", "로"), "이랑": ("이랑", "랑"), "랑": ("이랑", "랑")}
_VERBAL_RE = re.compile(r"^(는데|는지|는다|는|고|면|지만|니까|다|서|도록|려|며|겠|었|았|자|게|기)")

_CODE_RE = re.compile(
    r"(^[ \t]*(?:```|~~~)[^\n]*\n.*?^[ \t]*(?:```|~~~)[ \t]*$"
    r"|^[ \t]*(?:```|~~~).*\Z"
    r"|(?:^(?: {4}|\t)[^\n]*\n?)+"
    r"|`[^`\n]+`)",
    re.S | re.M,
)
# Pasted terminal / log material: prompts, continuation marks, ANSI colour codes,
# stack traces, timestamps. Such lines are not anyone's prose in any language.
_TERM_PROMPT_RE = re.compile(
    r"^\s*(?:PS\s+[A-Za-z]:[\\/][^>\n]*>"          # PowerShell
    r"|[A-Za-z]:[\\/][^>\n<|\"]*>(?!>)"            # cmd.exe
    r"|(?:\d+:\s*)?[a-z]{1,3}d>"                    # WinDbg kd> / 0:000>
    r"|\d+:\d+>"                                    # cdb 0:000>
    r"|>>>?(?:\s|$)|\.\.\.\s"                       # python, PowerShell continuation
    r"|[\w.-]+@[\w.-]+(?::[^\n$#]*)?[$#%](?:\s|$)"  # user@host:~$
    r"|[$%]\s+\S)")                                 # $ command
_TERM_MISC_RE = re.compile(
    r"\x1b\[|\[\d{1,3}(?:;\d{1,3})*m|"              # ANSI colour codes (raw or stripped ESC)
    r"^\s*Traceback \(most recent call last\)|^\s*File \"[^\"]+\", line \d+|"
    r"^\s*at [\w.$<>]+\(.*\)\s*$|^\s*[\w.]+(?:Error|Exception|Warning)(?::|$)|"
    r"^\s*\[?\d{4}-\d\d-\d\d[ T]\d\d:\d\d|^\s*\d\d:\d\d:\d\d[.,]\d+|"
    r"^\s*(?:ERR|ERROR|WARN|WARNING|INFO|DEBUG|FATAL)[\]:\s]|"
    r"^\s*[+-]{3} [ab]/|^@@ ")


def is_technical(line):
    """True for a pasted terminal, log or stack-trace line (never prose to fix)."""
    if not line or not line.strip():
        return False
    if _TERM_PROMPT_RE.match(line) or _TERM_MISC_RE.search(line):
        return True
    body = line.strip()
    if re.search("[\uAC00-\uD7A3\u3040-\u30FF\u4E00-\u9FFF]", body) or len(body) < 12:
        return False
    sym = len(re.findall(r"[\\/|<>{}\[\]=$*;:_@#%^~`\"'()0-9.-]", body))
    return sym * 10 >= len(body.replace(" ", "")) * 4       # 40%+ symbols / digits


_NON_LATIN_LETTER = re.compile("[\u0370-\u1FFF\u3040-\u30FF\u3400-\u9FFF\uAC00-\uD7A3]")


def technical_lines(lines):
    """Indexes of terminal / log lines. A shell prompt line starts a block: the
    command's output follows it (often plain English: banners, status text), so
    every line after it counts as output until a line in another script or a
    question ends the block."""
    tech, block = set(), False
    for i, line in enumerate(lines):
        if _TERM_PROMPT_RE.match(line or ""):
            block = True
            tech.add(i)
            continue
        if block and line.strip() and (_NON_LATIN_LETTER.search(line) or line.rstrip().endswith("?")):
            block = False
        if block or is_technical(line):
            tech.add(i)
    return tech


def strip_technical(text):
    lines = (text or "").splitlines()
    tech = technical_lines(lines)
    return "\n".join(l for i, l in enumerate(lines) if i not in tech)


_FOREIGN_ASK_RE = re.compile(
    r"(일본어|영어|영문|중국어|한자|원문)[^\n]{0,15}?(작성|써|쓰|번역|초안|만들|보내|준비|표현|예시|문구|메일|그대로)"
    r"|번역해|외국어 허용"
)
_state = {}



# ------------------------------------------------------------ language model
try:
    with open(os.path.join(_HERE, "ja_kanji.txt"), encoding="utf-8") as _fh:
        _JA_HAN = set(_fh.read())
except OSError:
    _JA_HAN = set()

_HANGUL_RUN = re.compile("[\uAC00-\uD7A3\u1100-\u11FF\u3130-\u318F]+")
_KANA_RUN = re.compile("[\u3040-\u30FF\u31F0-\u31FF]+")
_HAN_RUN = re.compile("[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]+")


# ------------------------------------------------------------ language table
# code: (English name, Korean name, native names, script, stopwords for Latin/Cyrillic ID)
_LANGS = {
    "ko": ("Korean", "한국어", ["한국어", "한글", "우리말", "韓国語", "韩语", "韓語", "韩文"], "hangul", ""),
    "ja": ("Japanese", "일본어", ["일어", "日本語", "日语", "日語", "日文"], "ja", ""),
    "zh": ("Chinese", "중국어", ["중문", "中国語", "中文", "汉语", "漢語", "华语", "普通话"], "han", ""),
    "en": ("English", "영어", ["영문", "英語", "英语", "英文"], "latin",
           "the and is are you what how why can please this that with for does my of it be would should have which"),
    "es": ("Spanish", "스페인어", ["Español", "Castellano", "スペイン語", "西班牙语"], "latin",
           "el la los las que de y es por para cómo qué una un del se no con está son pero muy"),
    "fr": ("French", "프랑스어", ["불어", "Français", "フランス語", "法语"], "latin",
           "le la les est et que pour une des comment pourquoi je vous nous pas avec dans sur ce sont"),
    "de": ("German", "독일어", ["Deutsch", "ドイツ語", "德语"], "latin",
           "der die das und ist nicht wie warum ich ein eine mit auf für sie es den dem sind auch"),
    "pt": ("Portuguese", "포르투갈어", ["Português", "ポルトガル語", "葡萄牙语"], "latin",
           "o os as que não como por uma é você para com do da em um são mas muito"),
    "it": ("Italian", "이탈리아어", ["Italiano", "イタリア語", "意大利语"], "latin",
           "il lo la gli le che di è per come perché una non con sono del della anche questo"),
    "nl": ("Dutch", "네덜란드어", ["Nederlands", "オランダ語", "荷兰语"], "latin",
           "de het een en is van dat niet hoe waarom ik je met voor zijn op ook wat"),
    "sv": ("Swedish", "스웨덴어", ["Svenska", "スウェーデン語", "瑞典语"], "latin",
           "och är att det som en på jag inte med för hur varför vad har den"),
    "da": ("Danish", "덴마크어", ["Dansk", "デンマーク語", "丹麦语"], "latin",
           "og er at det som en på jeg ikke med for hvordan hvorfor hvad har den"),
    "no": ("Norwegian", "노르웨이어", ["Norsk", "ノルウェー語", "挪威语"], "latin",
           "og er at det som en på jeg ikke med for hvordan hvorfor hva har den"),
    "fi": ("Finnish", "핀란드어", ["Suomi", "フィンランド語", "芬兰语"], "latin",
           "ja on ei että se mitä miten miksi minä sinä olen kanssa tämä mutta"),
    "pl": ("Polish", "폴란드어", ["Polski", "ポーランド語", "波兰语"], "latin",
           "i w nie się na jest że to jak dlaczego z do co ale czy dla"),
    "cs": ("Czech", "체코어", ["Čeština", "チェコ語", "捷克语"], "latin",
           "a je v se na že to jak proč s do co ale není jsem pro"),
    "ro": ("Romanian", "루마니아어", ["Română", "ルーマニア語", "罗马尼亚语"], "latin",
           "și este în nu că pe cu la ce cum de ce pentru sunt dar"),
    "hu": ("Hungarian", "헝가리어", ["Magyar", "ハンガリー語", "匈牙利语"], "latin",
           "és a az hogy nem egy van is mit miért hogyan meg de ez"),
    "tr": ("Turkish", "터키어", ["튀르키예어", "Türkçe", "トルコ語", "土耳其语"], "latin",
           "ve bir bu da de ne için nasıl neden ben sen değil ile çok mi mı"),
    "id": ("Indonesian", "인도네시아어", ["Bahasa Indonesia", "インドネシア語", "印尼语"], "latin",
           "dan yang di ini itu tidak dengan untuk apa bagaimana mengapa saya anda adalah ada"),
    "ms": ("Malay", "말레이어", ["Bahasa Melayu", "マレー語", "马来语"], "latin",
           "dan yang di ini itu tidak dengan untuk apa bagaimana kenapa saya awak adalah ialah"),
    "tl": ("Filipino", "필리핀어", ["타갈로그어", "Tagalog", "フィリピン語", "菲律宾语"], "latin",
           "ang ng sa mga at ay na hindi ko ka ito paano bakit ano"),
    "vi": ("Vietnamese", "베트남어", ["Tiếng Việt", "ベトナム語", "越南语"], "latin",
           "và là của có không được cho này một những tôi bạn như thế nào tại sao"),
    "sw": ("Swahili", "스와힐리어", ["Kiswahili", "スワヒリ語", "斯瓦希里语"], "latin",
           "na ya wa ni kwa za la katika hii hiyo jinsi kwa nini mimi wewe"),
    "ru": ("Russian", "러시아어", ["Русский", "ロシア語", "俄语"], "cyrillic",
           "и в не на что как почему я ты это с по для но он она"),
    "uk": ("Ukrainian", "우크라이나어", ["Українська", "ウクライナ語", "乌克兰语"], "cyrillic",
           "і в не на що як чому я ти це з по для але він вона"),
    "bg": ("Bulgarian", "불가리아어", ["Български", "ブルガリア語", "保加利亚语"], "cyrillic",
           "и в не на че как защо аз ти това с по за но той тя е"),
    "sr": ("Serbian", "세르비아어", ["Српски", "セルビア語", "塞尔维亚语"], "cyrillic",
           "и у не на да како зашто ја ти ово са по за али он она је"),
    "kk": ("Kazakh", "카자흐어", ["Қазақша", "カザフ語", "哈萨克语"], "cyrillic",
           "және бұл мен сен не қалай неге үшін бар емес"),
    "mn": ("Mongolian", "몽골어", ["Монгол", "モンゴル語", "蒙古语"], "cyrillic",
           "ба бол энэ тэр би чи юу яаж яагаад байна биш"),
    "el": ("Greek", "그리스어", ["Ελληνικά", "ギリシャ語", "希腊语"], "greek", ""),
    "ar": ("Arabic", "아랍어", ["العربية", "アラビア語", "阿拉伯语"], "arabic", ""),
    "fa": ("Persian", "페르시아어", ["فارسی", "ペルシア語", "波斯语"], "arabic", ""),
    "ur": ("Urdu", "우르두어", ["اردو", "ウルドゥー語", "乌尔都语"], "arabic", ""),
    "he": ("Hebrew", "히브리어", ["עברית", "ヘブライ語", "希伯来语"], "hebrew", ""),
    "hi": ("Hindi", "힌디어", ["हिन्दी", "हिंदी", "ヒンディー語", "印地语"], "devanagari", ""),
    "mr": ("Marathi", "마라티어", ["मराठी"], "devanagari", ""),
    "ne": ("Nepali", "네팔어", ["नेपाली"], "devanagari", ""),
    "bn": ("Bengali", "벵골어", ["বাংলা", "ベンガル語", "孟加拉语"], "bengali", ""),
    "pa": ("Punjabi", "펀자브어", ["ਪੰਜਾਬੀ"], "gurmukhi", ""),
    "gu": ("Gujarati", "구자라트어", ["ગુજરાતી"], "gujarati", ""),
    "ta": ("Tamil", "타밀어", ["தமிழ்", "タミル語", "泰米尔语"], "tamil", ""),
    "te": ("Telugu", "텔루구어", ["తెలుగు"], "telugu", ""),
    "kn": ("Kannada", "칸나다어", ["ಕನ್ನಡ"], "kannada", ""),
    "ml": ("Malayalam", "말라얄람어", ["മലയാളം"], "malayalam", ""),
    "si": ("Sinhala", "싱할라어", ["සිංහල"], "sinhala", ""),
    "th": ("Thai", "태국어", ["ภาษาไทย", "ไทย", "タイ語", "泰语"], "thai", ""),
    "lo": ("Lao", "라오어", ["ລາວ"], "lao", ""),
    "my": ("Burmese", "미얀마어", ["버마어", "မြန်မာ", "ミャンマー語", "缅甸语"], "myanmar", ""),
    "km": ("Khmer", "크메르어", ["캄보디아어", "ខ្មែរ", "クメール語", "高棉语"], "khmer", ""),
    "ka": ("Georgian", "조지아어", ["ქართული", "ジョージア語", "格鲁吉亚语"], "georgian", ""),
    "hy": ("Armenian", "아르메니아어", ["Հայերեն", "アルメニア語", "亚美尼亚语"], "armenian", ""),
    "am": ("Amharic", "암하라어", ["አማርኛ", "アムハラ語", "阿姆哈拉语"], "ethiopic", ""),
    "bo": ("Tibetan", "티베트어", ["བོད་ཡིག", "チベット語", "藏语"], "tibetan", ""),
}
LANG_NAMES = {k: v[0] for k, v in _LANGS.items()}
_KO_NAMES = {k: v[1] for k, v in _LANGS.items()}
_LANG_SCRIPT = {k: v[3] for k, v in _LANGS.items()}
_STOP = {k: set(v[4].split()) for k, v in _LANGS.items() if v[4]}
_ALIASES = {k: [v[0], v[1]] + v[2] for k, v in _LANGS.items()}

# script ranges (letters only); hangul/ja/han handled by the dedicated regexes
_SCRIPT_RANGES = {
    "cyrillic": "\u0400-\u04FF\u0500-\u052F", "greek": "\u0370-\u03FF\u1F00-\u1FFF",
    "arabic": "\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF",
    "hebrew": "\u0590-\u05FF\uFB1D-\uFB4F", "devanagari": "\u0900-\u097F", "bengali": "\u0980-\u09FF",
    "gurmukhi": "\u0A00-\u0A7F", "gujarati": "\u0A80-\u0AFF", "tamil": "\u0B80-\u0BFF",
    "telugu": "\u0C00-\u0C7F", "kannada": "\u0C80-\u0CFF", "malayalam": "\u0D00-\u0D7F",
    "sinhala": "\u0D80-\u0DFF", "thai": "\u0E00-\u0E7F", "lao": "\u0E80-\u0EFF",
    "tibetan": "\u0F00-\u0FFF", "myanmar": "\u1000-\u109F", "georgian": "\u10A0-\u10FF\u1C90-\u1CBF",
    "ethiopic": "\u1200-\u139F", "khmer": "\u1780-\u17FF", "armenian": "\u0530-\u058F",
}
_SCRIPT_RE = {s: re.compile("[%s]+" % r) for s, r in _SCRIPT_RANGES.items()}
_SCRIPT_DEFAULT = {"cyrillic": "ru", "greek": "el", "arabic": "ar", "hebrew": "he", "devanagari": "hi",
                   "bengali": "bn", "gurmukhi": "pa", "gujarati": "gu", "tamil": "ta", "telugu": "te",
                   "kannada": "kn", "malayalam": "ml", "sinhala": "si", "thai": "th", "lao": "lo",
                   "tibetan": "bo", "myanmar": "my", "georgian": "ka", "ethiopic": "am",
                   "khmer": "km", "armenian": "hy"}

# distinctive English words: a hit inside another Latin-script language is a leak
_EN_DISTINCT = {"the", "and", "however", "because", "actually", "which", "would", "should", "could",
                "this", "that", "these", "those", "with", "without", "really", "very", "although",
                "therefore", "basically", "usually", "probably", "maybe", "anyway", "though",
                "whether", "while", "where", "when", "what", "why", "how", "there", "their", "they",
                "have", "has", "been", "being", "will", "can't", "don't", "just", "only", "first",
                "then", "next", "finally", "salary", "deadline", "meeting", "schedule", "easy",
                "difficult", "good", "bad", "great", "something", "anything", "everything", "about"}
# words that are also ordinary words in some Latin languages
_EN_COLLIDE = {
    "de": {"will", "also", "was", "bin", "die", "hat", "man", "war", "check"},
    "nl": {"also", "then", "will", "check"}, "sv": {"then", "just"}, "da": {"then", "just"},
    "no": {"then", "just"}, "fr": {"point", "simple", "issue", "offer"},
    "es": {"simple"}, "pt": {"simple"}, "it": {"simple"}, "ro": {"simple"},
    "id": {"check"}, "ms": {"check"}, "tl": {"check"},
}

_ALIAS_TO_LANG = {a.lower(): l for l, al in _ALIASES.items() for a in al}
_ALIAS_ALT = "|".join(re.escape(a) for a in sorted(_ALIAS_TO_LANG, key=len, reverse=True))

# whole-answer commands (sticky for the session)
_FULL_CMD = [
    re.compile(r"(?P<l>" + _ALIAS_ALT + r")\s*(?:으로|로)\s*(?:만\s*)?(?:답|대답|답변|말|설명|대화|얘기|이야기|응답)", re.I),
    re.compile(r"(?:answer|respond|reply|speak|talk|explain|write (?:your|the) (?:answer|reply|response)s?)\s+(?:only\s+)?in\s+(?P<l>" + _ALIAS_ALT + r")", re.I),
    re.compile(r"(?:use|switch to)\s+(?P<l>" + _ALIAS_ALT + r")(?:\s+(?:only|from now on|please))?\s*[.!]?$", re.I | re.M),
    re.compile(r"(?P<l>" + _ALIAS_ALT + r")\s*で\s*(?:答え|回答|返答|返事|説明|話|会話)", re.I),
    re.compile(r"(?:用|以|请用|請用)\s*(?P<l>" + _ALIAS_ALT + r")\s*(?:回答|回复|回覆|答复|说明|說明|交流|对话|說|说)", re.I),
]
# partial foreign content: allow the language, keep the answer language
_PART_CMD = [
    re.compile(r"(?P<l>" + _ALIAS_ALT + r")\s*(?:으로|로)?\s*[^\n]{0,15}?(?:작성|써|쓰|번역|초안|만들|보내|준비|표현|예시|문구|메일|원문|그대로)", re.I),
    re.compile(r"(?:translate|draft|write|compose|phrase)[^\n]{0,40}?(?:in|into)\s+(?P<l>" + _ALIAS_ALT + r")", re.I),
    re.compile(r"(?P<l>" + _ALIAS_ALT + r")\s*(?:に翻訳|で書いて|の文面|に訳)", re.I),
    re.compile(r"(?:翻译成|翻譯成|译成|写成)\s*(?P<l>" + _ALIAS_ALT + r")", re.I),
]
_ALLOW_ALL_RE = re.compile(r"외국어 허용|allow foreign", re.I)

def _strip_noise(msg):
    msg = strip_technical(_CODE_RE.sub(" ", msg))
    msg = re.sub(r"https?://\S+|\S+[\\/]\S+", " ", msg)
    return "\n".join(l for l in msg.splitlines() if not l.lstrip().startswith(">"))


def _script_counts(text):
    c = {
        "ko": len(re.findall("[\uAC00-\uD7A3]", text)),
        "ja": len(re.findall("[\u3040-\u30FF]", text)),
        "han": len("".join(_HAN_RUN.findall(text))),
        "latin": len(re.findall(r"[A-Za-zÀ-ÿĀ-ž]", text)),
        "vi": len(re.findall("[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]", text, re.I)),
    }
    for s, rx in _SCRIPT_RE.items():
        c[s] = len("".join(rx.findall(text)))
    return c


def _latin_lang(text):
    words = re.findall(r"[a-zà-ÿā-ž']+", text.lower())
    if not words:
        return "en"
    ws = set(words)
    scores = {l: len(ws & st) for l, st in _STOP.items() if _LANG_SCRIPT[l] == "latin"}
    if len(re.findall("[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]", text, re.I)) >= 2:
        scores["vi"] = scores.get("vi", 0) + 3
    best = max(scores, key=scores.get)
    return best if scores[best] else "en"


def _cyrillic_lang(text):
    t = text.lower()
    if re.search("[іїєґ]", t):
        return "uk"
    if re.search("[әғқңөұүһі]", t):
        return "kk"
    if re.search("[өү]", t):
        return "mn"
    if re.search("[ђћџљњј]", t):
        return "sr"
    ws = set(re.findall(r"[\u0400-\u04FF]+", t))
    scores = {l: len(ws & _STOP[l]) for l in ("ru", "bg", "uk", "sr")}
    best = max(scores, key=scores.get)
    return best if scores[best] else "ru"


def _arabic_lang(text):
    if re.search("[ٹڈڑںےۓ]", text):
        return "ur"
    if re.search("[پچژگکی]", text):
        return "fa"
    return "ar"


def detect_language(text):
    """Best guess of the language a message is written in, or None if too short."""
    t = _strip_noise(text or "")
    c = _script_counts(t)
    others = {s: c[s] for s in _SCRIPT_RANGES}
    top_script = max(others, key=others.get)
    if c["ja"] >= 2 and c["ja"] * 3 >= c["han"]:
        return "ja"
    if c["ko"] >= 2 and c["ko"] >= c["han"] and c["ko"] >= others[top_script]:
        return "ko"
    if c["han"] >= 2 and c["han"] >= others[top_script]:
        return "ja" if c["ja"] else "zh"
    if others[top_script] >= 3 and others[top_script] * 2 >= c["latin"] // 3:
        if top_script == "cyrillic":
            return _cyrillic_lang(t)
        if top_script == "arabic":
            return _arabic_lang(t)
        if top_script == "devanagari":
            if re.search(r"\b(आहे|आणि|नाही)\b", t): return "mr"
            if re.search(r"(छ|हुन्छ|गर्नु)", t) and not re.search("है", t): return "ne"
            return "hi"
        return _SCRIPT_DEFAULT[top_script]
    if c["latin"] >= 8:
        return _latin_lang(t)
    return None


def _instruction_language(msg):
    """(language, rest) of the user's own sentences: the last real line decides, so a
    pasted foreign block followed by a question in another language works. `rest`
    is the remaining text (pasted / quoted material), used to allow those languages."""
    raw = [l for l in (msg or "").splitlines() if l.strip()]
    tech = technical_lines(raw)
    prose = [i for i, l in enumerate(raw) if i not in tech and not l.lstrip().startswith(">")]
    for idx in reversed(prose[-3:]):
        line = raw[idx]
        lang = detect_language(line)
        if lang:
            return lang, "\n".join(raw[:idx] + raw[idx + 1:])
    return detect_language(msg), ""


# "왜 영어로 대답했지?", "why did you answer in English?": talk about a past reply, not a request
_PAST_AFTER_RE = re.compile(r"\s*(?:했|하였|한\s|한거|한 거|된|되었|하지\s*않|안\s*했)")
# Korean requests: "영어로 대답해", "영어로 답해 줘", "영어로 말하라고", "영어로 답변 부탁해", "영어로만."
_KO_REQUEST_AFTER_RE = re.compile(
    r"(?:변|하)?\s*(?:을|를|은|는|도|만|좀)?\s*"
    r"(?:해|하세요|하십시오|하시오|하라|해라|하자|합시다|할\s*것|하도록|해야|하면|하기\s*바|하길|"
    r"줘|주세요|주십시오|주시|주라|부탁|바랍|바래|요청|[.!]?\s*$)")
_ASK_WHY_RE = re.compile(r"\b(?:why|did you|didn't you|have you|were you)\b|왜|어째서|なぜ|どうして|为什么|為什麼", re.I)


def _find_cmd(patterns, msg, commands_only=False):
    found = []
    msg = msg or ""
    for pat in patterns:
        for m in pat.finditer(msg):
            lang = _ALIAS_TO_LANG.get(m.group("l").lower())
            if not lang:
                continue
            if commands_only:
                a = msg.rfind("\n", 0, m.start()) + 1
                b = msg.find("\n", m.end())
                line = msg[a: b if b >= 0 else len(msg)]
                if _PAST_AFTER_RE.match(msg, m.end()) or _ASK_WHY_RE.search(line):
                    continue
                if pat is patterns[0] and not _KO_REQUEST_AFTER_RE.match(msg, m.end()):
                    continue   # "영어로 대답하고", "영어로 답하는": a description, not a request
            found.append((m.start(), lang))
    return [l for _, l in sorted(found)]


def _guard_prefixes():
    texts = list(_GUARDS.values()) + list(_GUARDS_SHORT.values()) + [_GUARD_GENERIC, _GUARD_SHORT_GENERIC]
    return tuple(sorted({t[:t.index("]") + 1] for t in texts if "]" in t[:40]} | {"[pure-language]"}))


# notices the host appends to the user's message ("[System: The skills ... changed ...]")
# A notice may span lines (the skill list), so it may run up to 40 lines; each line is
# scanned with [^\n] only, which keeps the match linear (no backtracking across lines).
_HOST_NOTE_RE = re.compile(r"^[ \t]*\[(?:System|SYSTEM|IMPORTANT|Note|NOTE|Context|CONTEXT)\b"
                           r"(?:[^\n]*\n){0,40}?[^\n]*\][ \t]*$", re.M)
_HOST_TURN_RE = re.compile(r"\s*\[(?:System|SYSTEM|IMPORTANT)\s*:")
# one-line wrapper tags ("[OUT-OF-BAND USER MESSAGE — ...]", "[/OUT-OF-BAND USER MESSAGE]")
# Words and separators never overlap and the tail is capped, so matching stays linear.
_HOST_TAG_RE = re.compile(r"^[ \t]*\[/?[A-Z][A-Z0-9]*(?:[ _-][A-Z0-9]+)+\b[^\]\n]{0,200}\][ \t]*$", re.M)


def strip_host_notes(msg):
    """The user's own words: drop host notices and this plugin's rule lines."""
    if _HOST_TURN_RE.match(msg or ""):
        return ""   # the whole message is the host's (background job finished, nudges)
    msg = _HOST_TAG_RE.sub("", _HOST_NOTE_RE.sub("", msg or ""))
    prefixes = _guard_prefixes()
    return "\n".join(l for l in msg.splitlines() if not l.lstrip().startswith(prefixes)).strip()


_GUARDS = {
    "ko": _GUARD,
    "en": ("[Output rule] Write this entire answer, including progress notes between tool calls, "
           "in English only. Do not mix Korean, "
           "Chinese or Japanese characters or words into sentences. Proper nouns, acronyms, "
           "code and paths are fine. Put any foreign text that must appear inside backticks "
           "or a code block. Anything else is checked automatically."),
    "ja": ("[出力ルール] この回答はすべて日本語で書くこと。文中に韓国語（ハングル）、"
           "中国語の簡体字、英単語を混ぜないこと。固有名詞・略語・コード・パスは例外。"
           "外国語の原文が必要な場合はバッククォートかコードブロックに入れること。"
           "それ以外の外国文字は自動で検査される。"),
    "zh": ("[输出规则] 本回答必须全部使用中文。句中不要混入韩文、日文假名或英文单词。"
           "专有名词、缩写、代码和路径除外。需要外语原文时请放在反引号或代码块中。"
           "其他外文字符会被自动检查。"),
}
# Short guards: injected every turn and kept in the conversation history, so every
# token here is paid again on each later call. The full guards above are used only
# on the turn after a leak was found.
_GUARDS_SHORT = {
    "ko": ("[출력 규칙] 진행 설명까지 모두 한국어로 쓴다. 외국어 용어는 한국어로 옮기고, "
           "원문이 꼭 필요하면 처음 한 번만 뒤에 괄호와 백틱으로 덧붙인다. 예: 내정 취소(`内定取消し`)"),
    "en": ("[Output rule] Whole reply, progress notes included, in English. Translate foreign terms; "
           "if the original is needed, add it once after the English in parentheses and backticks."),
    "ja": ("[出力ルール] 途中経過も含め全て日本語で書く。外国語の用語は日本語に訳し、"
           "原文が必要なら初出のみ訳語の後に括弧とバッククォートで添える。"),
    "zh": "[输出规则] 包括过程说明在内全部用中文。外文术语要译成中文，必要时仅首次在译文后用括号和反引号附原文。",
}
_GUARD_SHORT_GENERIC = ("[Output rule] Whole reply, progress notes included, in {name}. Translate "
                        "foreign terms; if the original is needed, add it once after the translation in "
                        "parentheses and backticks.")
_ALLOW_SHORT = {"ko": " 허용: {names}.", "ja": " 許可: {names}。", "zh": " 允许: {names}。"}
_GUARD_GENERIC = ("[Output rule] Write this entire answer in {name} only, the language of the "
                  "user's message, including progress notes between tool calls; pasted command output "
                  "in another language does not change it. Do not mix in words or characters from other languages. "
                  "Proper nouns, acronyms, code and paths are fine. Put any foreign text that "
                  "must appear inside backticks or a code block.")
_ALLOW_NOTE = {
    "ko": " 단, 사용자가 요청한 {names} 부분은 허용한다(가능하면 코드 블록 안에 쓴다).",
    "en": " Text in {names} that the user asked for is allowed (prefer a code block).",
    "ja": " ただし、ユーザーが求めた{names}の部分は許可する（できればコードブロックに入れる）。",
    "zh": " 但用户要求的{names}内容可以保留（最好放在代码块中）。",
}



def requested_languages(msg):
    """Languages the user explicitly asked to appear (draft in Japanese, translate into
    French). Only these are announced in the guard; languages of pasted material are
    tolerated as quotes but never advertised, or the model takes them as permission."""
    return set(_find_cmd(_PART_CMD, msg or ""))


def guard_for(target, allowed, full=False):
    """The rule text injected before the answer. ``full`` (after a leak) is the long,
    explicit version; otherwise a one-line reminder keeps the per-turn cost low."""
    name = LANG_NAMES.get(target, target)
    if full:
        base = _GUARDS.get(target) or _GUARD_GENERIC.format(name=name)
    else:
        base = _GUARDS_SHORT.get(target) or _GUARD_SHORT_GENERIC.format(name=name)
    extra = sorted(allowed - {target})
    if extra:
        if target == "ko":
            names = ", ".join(_KO_NAMES.get(l, l) for l in extra)
        else:
            names = ", ".join(LANG_NAMES.get(l, l) for l in extra)
        notes = _ALLOW_NOTE if full else _ALLOW_SHORT
        base += notes.get(target, _ALLOW_NOTE["en"] if full else " Allowed: {names}.").format(names=names)
    return base


# private-use placeholder put after a leak that stays unfixed; clean() turns it into ⚠ or drops it
_MK = "\uE0A0"


# ------------------------------------------------- non-Korean target scanning
def _scan_foreign(line, target, allowed, exempt, user_text, res, final, scripts_only=False):
    """Find foreign-script leaks. Leaks are rewritten by the model (final=False)
    or marked (final=True; the original is kept, see ``clean(mark=...)``). scripts_only: Korean target, where Hangul,
    kana, Han and Latin are handled by the dedicated Korean pipeline."""
    ok_scripts = {_LANG_SCRIPT.get(l, "latin") for l in allowed} | {_LANG_SCRIPT.get(target, "latin")}
    spans = []
    if not scripts_only:
        if "hangul" not in ok_scripts:
            spans += [(m.start(), m.end()) for m in _HANGUL_RUN.finditer(line)]
        if "ja" not in ok_scripts:
            spans += [(m.start(), m.end()) for m in _KANA_RUN.finditer(line)]
        if not ({"ja", "han"} & ok_scripts):
            spans += [(m.start(), m.end()) for m in _HAN_RUN.finditer(line)]
        elif "ja" in ok_scripts and "han" not in ok_scripts and _JA_HAN:
            for m in _HAN_RUN.finditer(line):
                for k, ch in enumerate(m.group(0)):
                    if ch not in _JA_HAN:
                        spans.append((m.start() + k, m.start() + k + 1))
    for s, rx in _SCRIPT_RE.items():
        if s not in ok_scripts:
            spans += [(m.start(), m.end()) for m in rx.finditer(line)]
    if not scripts_only:
        if "latin" not in ok_scripts:
            for m in _LATIN_RE.finditer(line):
                w = m.group(1); low = w.lower()
                if w.isupper() or w[0].isupper() or low in _LATIN_ALLOW or low in exempt:
                    continue
                if low in _LATIN_COMMON:
                    spans.append((m.start(), m.end()))
        elif target != "en" and "en" not in allowed and _LANG_SCRIPT.get(target) == "latin":
            bad = _EN_DISTINCT - _STOP.get(target, set()) - _EN_COLLIDE.get(target, set())
            for m in _LATIN_RE.finditer(line):
                w = m.group(1); low = w.lower()
                if low in bad and low not in exempt and (w.islower() or w[1:].islower()):
                    spans.append((m.start(), m.end()))
    spans = [s for s in spans if line[s[0]:s[1]] not in user_text]
    if not spans:
        return line
    spans.sort()
    for a, b in spans:
        res.hits.append(("%s-%s" % (target, "mark" if final else "rewrite"), line[a:b],
                         line[max(0, a - 20): b + 20]))
    if not final:
        res.unresolved = True
        return line
    out, pos = [], 0
    for a, b in spans:
        if a < pos:
            continue
        out.append(line[pos:b]); out.append(_MK); pos = b
    out.append(line[pos:])
    return "".join(out)


# ------------------------------------------------------------- Korean helpers
def _jong(ch):
    return (ord(ch) - 0xAC00) % 28 if "\uAC00" <= ch <= "\uD7A3" else 0


def _compose(cho, jung, jong):
    return chr(0xAC00 + (cho * 21 + jung) * 28 + jong)


def _split(ch):
    i = ord(ch) - 0xAC00
    return i // 588, (i % 588) // 28, i % 28


_Y_VOWELS = {2, 6, 7, 12, 17, 20}   # ㅑ ㅕ ㅖ ㅛ ㅠ ㅣ


def _initial_rule(syl):
    """두음법칙 for a word-initial syllable: 녀→여, 력→역, 락→낙."""
    if not ("\uAC00" <= syl <= "\uD7A3"):
        return syl
    cho, jung, jong = _split(syl)
    if cho == 5:                                   # ㄹ
        return _compose(11 if jung in _Y_VOWELS else 2, jung, jong)
    if cho == 2 and jung in _Y_VOWELS:            # ㄴ + y-vowel
        return _compose(11, jung, jong)
    return syl


def _medial_rule(prev, syl):
    """렬/률 after a vowel or ㄴ become 열/율 (비율, 진열)."""
    if syl in ("렬", "률") and prev and (_jong(prev) in (0, 4)):
        return "열" if syl == "렬" else "율"
    return syl


def _read_hanja(run):
    out = []
    for i, ch in enumerate(run):
        r = _READ.get(ch)
        if not r:
            return None
        r = _initial_rule(r) if i == 0 else _medial_rule(out[-1], r)
        out.append(r)
    return "".join(out)


def _fix_particle(repl, rest):
    m = _PARTICLE_RE.match(rest)
    if not m or not repl:
        return rest, 0
    cur = m.group(1)
    with_b, without_b = _PAIR[cur]
    j = _jong(repl[-1])
    want = with_b if j else without_b
    if cur in ("으로", "로") and j == 8:
        want = "로"
    return want, len(cur)


# ------------------------------------------------------------------ cleaning
class _Line:
    __slots__ = ("text", "hits", "unresolved")

    def __init__(self):
        self.text, self.hits, self.unresolved = "", [], False


def _clean_cjk(seg, user_cjk, res, final):
    for src, dst in _PUNCT_MAP.items():
        if src in seg:
            seg = seg.replace(src, dst)
    if not _CJK_RE.search(seg):
        return seg
    out, pos = [], 0
    for m in _CJK_RE.finditer(seg):
        if m.start() < pos:
            continue
        run = m.group(0)
        out.append(seg[pos:m.start()])
        pos = m.end()
        if run in user_cjk:
            out.append(run)
            continue
        ctx = seg[max(0, m.start() - 20): m.end() + 20]
        parts, last = [], 0
        for w in _WORD_RE.finditer(run):          # known words first
            res.hits.append(("cjk-word", w.group(0), ctx))
            parts.append((run[last:w.start()], False))
            parts.append((_WORDS[w.group(0)], True))
            last = w.end()
        parts.append((run[last:], False))
        text = ""
        for piece, done in parts:
            if done or not piece:
                text += piece
                continue
            needs_rewrite = (_KANA_RE.search(piece) or any(c in _ZH_FUNC for c in piece)
                             or _read_hanja(piece) is None)
            if needs_rewrite and not final:
                res.unresolved = True
                res.hits.append(("cjk-rewrite", piece, ctx))
                text += piece                    # keep original for the rewrite call
                continue
            reading = _read_hanja(piece)
            if reading and not _KANA_RE.search(piece) and not any(c in _ZH_FUNC for c in piece):
                res.hits.append(("cjk-read", piece, ctx))
                text += reading
            else:
                res.hits.append(("cjk-mark", piece, ctx))
                text += piece + _MK
        if text and text[0] != _MK and "\uAC00" <= text[0] <= "\uD7A3":
            prev = seg[m.start() - 1] if m.start() else ""
            if not _HANGUL_CH.match(prev):        # word start -> 두음법칙 on first syllable
                text = _initial_rule(text[0]) + text[1:]
        out.append(text)
        ptxt, used = _fix_particle(text, seg[pos:])
        if used:
            out.append(ptxt)
            pos += used
    out.append(seg[pos:])
    return "".join(out)


_PHRASE_RE = re.compile(r"(?<![A-Za-z0-9_./\\:@#%=&?+~`-])[a-z]{2,}(?:[ -][a-z]{2,})+(?![A-Za-z0-9_/\\:@#%=&?+~`-]|\.[A-Za-z0-9_])")


_PHRASE_ALLOW = {"pull request", "merge request", "hot reload", "dark mode", "light mode"}


def _english_phrases(seg, exempt):
    """Two or more lowercase English words in a row inside Korean prose."""
    found = []
    for m in _PHRASE_RE.finditer(seg):
        words = re.split(r"[ -]", m.group(0))
        if words[0] in _LATIN_ALLOW or all(w in exempt for w in words):
            continue                              # a command (git push, npm install) or user's words
        if m.group(0) in _PHRASE_ALLOW:
            continue
        if not _HANGUL_CH.search(seg[max(0, m.start() - 30): m.end() + 30]):
            continue
        found.append(m)
    return found


_PROSE_SUFFIX = re.compile(r"(?:ly|tion|sion|ment|ness|ive|ful|ous|able|ible|ity|ance|ence)$")


def _clean_latin(seg, exempt, res, final, glued_only=False):
    phrases = [] if glued_only else _english_phrases(seg, exempt)
    if phrases:
        for m in phrases:
            res.hits.append(("en-phrase-" + ("mark" if final else "rewrite"), m.group(0),
                             seg[max(0, m.start() - 20): m.end() + 20]))
        if not final:
            res.unresolved = True
        else:
            out, pos = [], 0
            for m in phrases:
                out.append(seg[pos:m.end()]); out.append(_MK); pos = m.end()
            out.append(seg[pos:])
            seg = "".join(out)
    out, pos = [], 0
    for m in _LATIN_RE.finditer(seg):
        if m.start() < pos:
            continue
        word = m.group(1)
        low = word.lower()
        if word.isupper():
            continue                              # ACRONYM
        if word[0].isupper():                     # Proper noun unless a plain common word
            if not word[1:].islower() or low in _PROPER_COLLIDE or low not in _LATIN_COMMON:
                continue
        if low in _LATIN_ALLOW or low in exempt:
            continue
        before = seg[m.start() - 1] if m.start() else ""
        after = seg[m.end()] if m.end() < len(seg) else ""
        glued = bool(_HANGUL_CH.match(before) or _HANGUL_CH.match(after))
        if glued_only and not glued:
            continue                              # English is allowed as a quote
        if not glued and low not in _LATIN_COMMON:
            # an uncommon lowercase word standing alone in a Korean sentence (… legally 다음을 …):
            # only clear prose word shapes, so identifiers (cron, diff, msiexec) cost no rewrite
            if not (word.islower() and len(word) >= 5 and _PROSE_SUFFIX.search(word)
                    and (_HANGUL_CH.search(seg[max(0, m.start() - 12): m.start()])
                         or _HANGUL_CH.search(seg[m.end(): m.end() + 12]))):
                continue
        if not _HANGUL_CH.search(seg[max(0, m.start() - 40): m.end() + 40]):
            continue                              # a genuinely English passage
        ctx = seg[max(0, m.start() - 20): m.end() + 20]
        out.append(seg[pos:m.start()])
        pos = m.end()
        if low in _LATIN_WORDS:
            repl = _LATIN_WORDS[low]
            res.hits.append(("en-fixed", word, ctx))
            rest = seg[pos:]
            ptxt, used = _fix_particle(repl, rest)
            if used:
                out.append(repl + ptxt)
                pos += used
            elif low not in _LATIN_VERB_STEMS and _VERBAL_RE.match(rest):
                out.append(repl + "하")             # check는데 -> 확인하는데
            else:
                out.append(repl)
        elif not final:
            res.unresolved = True
            res.hits.append(("en-rewrite", word, ctx))
            out.append(word)
        else:
            res.hits.append(("en-mark", word, ctx))
            out.append(word + _MK)
    out.append(seg[pos:])
    return "".join(out)


_TARGET_CHARS = {"hangul": "\uAC00-\uD7A3", "ja": "\u3040-\u30FF\u4E00-\u9FFF", "han": "\u4E00-\u9FFF",
                 "latin": "A-Za-z\u00C0-\u024F"}
_TECH_CH = re.compile(r"[\\/:._=<>|*\[\]{}()'\"$#@-]")


def _verbatim_spans(line, target, context):
    """Stretches of ``line`` written in another script that appear word for word in
    ``context`` (the user's message, tool / command output of this turn): quoted
    output, error messages, file names. Short single words never qualify, so a
    leaked word stays caught even when the source material contains it."""
    if not context:
        return []
    script = _LANG_SCRIPT.get(target, "latin")
    chars = _TARGET_CHARS.get(script)
    if chars is None:
        rng = _SCRIPT_RANGES.get(script)
        chars = rng if isinstance(rng, str) else None
    if not chars:
        return []
    spans = []
    for m in re.finditer("[^%s\\n]+" % chars, line):
        a, b = m.start(), m.end()
        while a < b and line[a] in " \t,.;!?()\"'“”‘’「」": a += 1
        while b > a and line[b - 1] in " \t,.;!?()\"'“”‘’「」": b -= 1
        piece = line[a:b]
        if not piece or piece not in context:
            continue
        cjk = bool(_CJK_RE.search(piece) or _HANGUL_RUN.search(piece))
        if (len(piece.split()) >= 3 or len(piece) >= 16 or (cjk and len(piece) >= 10)
                or (_TECH_CH.search(piece) and len(piece) >= 6)):
            spans.append((a, b))
    return spans


def _clean_line(line, exempt, user_cjk, final=False, target="ko", allowed=frozenset(), user_text="",
                context=""):
    spans = _verbatim_spans(line, target, context)
    if not spans:
        return _clean_line_raw(line, exempt, user_cjk, final, target, allowed, user_text)
    keep, masked, pos = [], [], 0
    for k, (a, b) in enumerate(spans):
        masked.append(line[pos:a]); masked.append(chr(0xE000 + k)); keep.append(line[a:b]); pos = b
    masked.append(line[pos:])
    res = _clean_line_raw("".join(masked), exempt, user_cjk, final, target, allowed, user_text)
    for k, piece in enumerate(keep):
        res.text = res.text.replace(chr(0xE000 + k), piece)
    return res


_GLOSS_RE = re.compile("([\u3040-\u30FF\u3400-\u9FFF\uF900-\uFAFF]+)\\(([\uAC00-\uD7A3][\uAC00-\uD7A3 ·]*)\\)")


def _fix_gloss(line, quote_ok):
    """``内定(내정)``: the Korean gloss is already there. Keep it as the word and the
    original only as a quote when quoting is allowed: 내정(`内定`), else 내정."""
    return _GLOSS_RE.sub((lambda m: "%s(`%s`)" % (m.group(2), m.group(1))) if quote_ok
                         else (lambda m: m.group(2)), line)


def _glued(text, m):
    before = text[m.start() - 1] if m.start() else ""
    after = text[m.end()] if m.end() < len(text) else ""
    return bool(_HANGUL_CH.match(before) or _HANGUL_CH.match(after))


def _masked(text, rx, pick, fn):
    """Run ``fn`` on ``text`` with the ``rx`` matches that ``pick`` rejects hidden."""
    keep = []

    def hide(m):
        if pick(m):
            return m.group(0)
        keep.append(m.group(0))
        return chr(0xF0000 + len(keep) - 1)
    out = fn(rx.sub(hide, text))
    for k, piece in enumerate(keep):
        out = out.replace(chr(0xF0000 + k), piece)
    return out


def _clean_line_raw(line, exempt, user_cjk, final=False, target="ko", allowed=frozenset(), user_text=""):
    res = _Line()
    if target != "ko":
        res.text = _scan_foreign(line, target, allowed, exempt, user_text, res, final)
        return res
    t = _fix_gloss(line, quote_ok=bool({"ja", "zh"} & allowed))
    if not ({"ja", "zh"} & allowed):
        t = _clean_cjk(t, user_cjk, res, final)
    else:
        # Another language may be quoted, but not woven into a Korean sentence:
        # a run glued to Hangul (日本の中途採用에서는) or Chinese grammar when only
        # Japanese is allowed (취소理由是) is still a leak.
        t = _masked(t, _CJK_RE, lambda m: _glued(t, m) or ("zh" not in allowed and
                                                            any(c in _ZH_FUNC for c in m.group(0))),
                    lambda x: _clean_cjk(x, user_cjk, res, final))
    t = _clean_latin(t, exempt, res, final, glued_only="en" in allowed)
    if not res.unresolved or final:
        t = _scan_foreign(t, target, allowed, exempt, user_text, res, final, scripts_only=True)
    else:
        _scan_foreign(t, target, allowed, exempt, user_text, res, final, scripts_only=True)
    res.text = t
    return res


def rewrite_instructions(target, allowed, sample):
    name = LANG_NAMES.get(target, target)
    text = ("Each line mixes in words from other languages. Rewrite each line entirely in natural "
            "%s, translating every foreign word; same meaning. " % name)
    if sample and target not in LANG_NAMES:
        text += "Target language = language of: %s. " % sample[:120]
    keep = sorted(allowed - {target})
    if keep:
        text += "Keep %s text as is. " % ", ".join(LANG_NAMES.get(l, l) for l in keep)
    text += ("Keep markdown, numbers, `code`, URLs, paths, names, ACRONYMS. "
             "Same line count and order, no numbering.")
    return text


def rewrite_max_tokens(lines):
    """Output budget for a rewrite: roughly the input size plus headroom."""
    return 64 + sum(len(l) for l in lines) * 2


REWRITE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["lines"],
                  "properties": {"lines": {"type": "array", "items": {"type": "string"}}}}


def number_lines(lines):
    return "\n".join("%d\t%s" % (i + 1, l) for i, l in enumerate(lines))


def parse_rewrite(parsed, n):
    """Validate a structured rewrite answer; returns n strings or None."""
    if not isinstance(parsed, dict) or not isinstance(parsed.get("lines"), list):
        return None
    got = [re.sub(r"^\d+\t", "", str(x)) for x in parsed["lines"]]
    return got if len(got) == n else None


_INLINE_KEEP = re.compile(r"`[^`\n]+`|https?://\S+|\[[^\]]*\]\([^)]*\)")
_PREFIX_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)]|#{1,6}|>|\|)?\s*")


def _structure_ok(orig, new):
    """A rewrite must keep the line's markdown skeleton: list/heading prefix,
    table pipes, inline code, links and URLs, and must not explode in length."""
    if not isinstance(new, str) or not new.strip():
        return False
    if "\n" in new.strip():
        return False
    if _PREFIX_RE.match(orig).group(0).strip() != _PREFIX_RE.match(new).group(0).strip():
        return False
    if orig.count("|") != new.count("|"):
        return False
    if any(tok not in new for tok in _INLINE_KEEP.findall(orig)):
        return False
    return len(new) <= max(40, len(orig) * 3)


_FENCE = re.compile(r"^[ \t]*(```|~~~)")
_INLINE_CODE = re.compile(r"`[^`\n]+`")


def _prose_indexes(lines):
    """Indexes of lines outside fenced code blocks that are not terminal / log lines."""
    out, fenced = [], False
    tech = technical_lines(lines)
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced and i not in tech:
            out.append(i)
    return out


def _target_chars(target):
    script = _LANG_SCRIPT.get(target, "latin")
    chars = _TARGET_CHARS.get(script)
    if chars is None:
        rng = _SCRIPT_RANGES.get(script)
        chars = rng if isinstance(rng, str) else ""
    return chars


def _letters(text):
    return [c for c in text if c.isalpha()]


def wrong_language(text, target, allowed=frozenset(), min_letters=40):
    """The language a whole answer is written in when it is not the target (an
    English answer to a Korean conversation), else None. Code, URLs, paths and
    terminal output are ignored."""
    lines = (text or "").split("\n")
    prose = "\n".join(_INLINE_CODE.sub(" ", lines[i]) for i in _prose_indexes(lines))
    prose = re.sub(r"https?://\S+|\S+[\\/]\S+", " ", prose)
    letters = _letters(prose)
    if len(letters) < min_letters:
        return None
    lang = detect_language(prose)
    if not lang or lang == target or lang in allowed:
        return None
    chars = _target_chars(target)
    if not chars:
        return None
    own = len(re.findall("[%s]" % chars, prose))
    if own * 100 >= len(letters) * 15:          # mostly mixed, not wholly foreign
        return None
    return lang


def _whole_rewrite(text, target, allowed, user_text, rewriter, lang, hits, limit=80):
    lines = text.split("\n")
    chars = _target_chars(target)
    idxs = []
    for i in _prose_indexes(lines):
        body = _INLINE_CODE.sub(" ", lines[i])
        if len([c for c in _letters(body) if not re.match("[%s]" % chars, c)]) >= 3:
            idxs.append(i)
    if not idxs:
        return text
    hits.append(("whole-%s" % ("rewrite" if rewriter else "mark"), lang, lines[idxs[0]].strip()[:60]))
    if rewriter is None:
        return text
    idxs = idxs[:limit]
    try:
        got = rewriter([lines[i] for i in idxs], target, allowed, user_text) or []
    except Exception as exc:  # the answer must never be lost
        log.warning("pure-language: whole-answer rewrite failed: %s", exc)
        return text
    for i, g in zip(idxs, got):
        if g and g.strip() and _structure_ok(lines[i], g) and re.search("[%s]" % chars, g):
            hits.append(("rewritten", lines[i].strip()[:60], g.strip()[:60]))
            lines[i] = g
    return "\n".join(lines)


def _sentences(line):
    """(start, end) of sentences in a line: split after . ! ? 。 when whitespace
    follows and the point is outside inline code."""
    out, start, ticks = [], 0, 0
    for k, ch in enumerate(line):
        if ch == "`":
            ticks += 1
        elif (ch in ".!?。！？" and ticks % 2 == 0 and k + 1 < len(line) and line[k + 1] == " "):
            end = k + 1
            while end < len(line) and line[end] == " ":
                end += 1
            out.append((start, k + 1))
            start = end
    if start < len(line):
        out.append((start, len(line)))
    return out


def clean(text, user_words=(), user_cjk=(), rewrite=True, target="ko", allowed=frozenset(), user_text="",
          rewriter=None, max_lines=20, context="", local_first=False, mark=False):
    """Returns (cleaned_text, hits). Pure apart from the optional rewrite call.

    With a rewriter, every line that leaks is rewritten by the model: word tables and
    Hanja readings can read awkwardly (Japanese ``書類選考`` read as 서류선고), so they
    are only the fallback when the rewrite fails. ``local_first=True`` restores the
    old order (rules first, the model only for what they cannot fix).

    ``mark``: put ⚠ after what neither the model nor the rules could fix (default: leave
    it as written).

    ``context``: other material of this turn the answer may quote word for word
    (tool and command output). Terminal, log and stack-trace lines are never touched."""
    allowed = frozenset(allowed)
    pre_hits = []
    lang = wrong_language(text, target, allowed)
    if lang:                                  # the whole answer is in another language
        text = _whole_rewrite(text, target, allowed, user_text, rewriter if rewrite else None,
                              lang, pre_hits, limit=max(60, int(max_lines)))
        return text, pre_hits                 # never sprinkle marks over an untranslated answer
    context = (user_text or "") + "\n" + (context or "")
    parts = _CODE_RE.split(text)
    code_terms = {w.lower() for c in parts[1::2] for w in re.findall(r"[A-Za-z]{2,}", c)}
    exempt = set(user_words) | code_terms
    user_cjk = set(user_cjk)
    kw = dict(target=target, allowed=allowed, user_text=user_text, context=context)
    model_all = bool(rewrite and rewriter is not None and not local_first)
    needs = (lambda r: r.unresolved or bool(r.hits)) if model_all else (lambda r: r.unresolved)
    hits, pending, split_parts = pre_hits, [], {}
    for i in range(0, len(parts), 2):
        lines = parts[i].split("\n")
        tech = technical_lines(lines)
        for j, line in enumerate(lines):
            if j in tech:
                continue                          # pasted prompt / output / trace line
            r = _clean_line(line, exempt, user_cjk, final=not rewrite, **kw)
            hits.extend(r.hits)
            if needs(r):
                pending.append((i, j, line))
            lines[j] = r.text
        split_parts[i] = lines
    if pending:
        ck = lambda o: (target, tuple(sorted(allowed)), o)
        # Long lines: send only the sentences that leak, so the model writes (and bills)
        # far fewer output tokens than the whole paragraph.
        plans = []                                 # per pending line: [(start, end)] units
        for _, _, orig in pending:
            units = []
            if len(orig) > 80:
                for a, b in _sentences(orig):
                    if needs(_clean_line(orig[a:b], exempt, user_cjk, final=False, **kw)):
                        units.append((a, b))
            plans.append(units or [(0, len(orig))])
        todo = [orig[a:b] for (_, _, orig), units in zip(pending, plans) for a, b in units]
        todo = [u for u in dict.fromkeys(todo) if ck(u) not in _REWRITE_CACHE][:max(1, int(max_lines))]
        if todo and rewrite and rewriter is not None:
            got = []
            try:
                got = rewriter(todo, target, allowed, user_text) or []
            except Exception as exc:  # the answer must never be lost
                log.warning("pure-language: rewriter failed: %s", exc)
            for o, g in zip(todo, got):
                if _structure_ok(o, g):
                    if len(_REWRITE_CACHE) >= _CACHE_MAX:
                        _REWRITE_CACHE.pop(next(iter(_REWRITE_CACHE)))
                    _REWRITE_CACHE[ck(o)] = g
        for (i, j, orig), units in zip(pending, plans):
            cand, pos, used = [], 0, 0
            for a, b in units:                    # apply each verified unit; keep the rest
                g = _REWRITE_CACHE.get(ck(orig[a:b]))
                cand.append(orig[pos:a])
                g = (g if (a, b) == (0, len(orig)) else (g or "").strip()) if g and g.strip() else None
                if g is not None and not any(h[0].endswith("mark") for h in
                                             _clean_line(g, exempt, user_cjk, final=True, **kw).hits):
                    cand.append(g)
                    used += 1
                else:
                    cand.append(orig[a:b])
                pos = b
            cand.append(orig[pos:])
            fixed = _clean_line("".join(cand), exempt, user_cjk, final=True, **kw)   # rules + marks for leftovers
            if used:
                hits.append(("rewritten", orig.strip()[:60], fixed.text.strip()[:60]))
            hits.extend(h for h in fixed.hits if h[0].endswith(("mark", "read")))
            split_parts[i][j] = fixed.text
    for i, lines in split_parts.items():
        parts[i] = "\n".join(lines)
    # what neither the model nor the rules could fix: left as written, or flagged with ⚠
    return "".join(parts).replace(_MK, "⚠" if mark else ""), hits


# ------------------------------------------------------------------ logging
_LOG_MAX = 1_000_000


def write_log(path, session_id, hits):
    """Append hit snippets (never whole answers); rotate at 1 MB."""
    if not path or not hits:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > _LOG_MAX:
            os.replace(path, path + ".1")
        with open(path, "a", encoding="utf-8") as fh:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            for h in hits:
                kind, word, ctx = (list(h) + ["", "", ""])[:3]
                fh.write("%s\t%s\t%s\t%s\t%s\n" % (stamp, session_id, kind, word,
                                                   str(ctx).replace("\n", " ").replace("\t", " ")))
    except OSError as exc:
        log.warning("pure-language: log write failed: %s", exc)


def leaks(text, user_words=(), user_cjk=(), target="ko", allowed=frozenset(), user_text="", context=""):
    """Hits for a finished answer without rewriting anything (CLI adapters)."""
    _, hits = clean(text, user_words, user_cjk, rewrite=False, target=target,
                    allowed=allowed or frozenset({target}), user_text=user_text, context=context)
    return [h for h in hits if h[0] != "rewritten"]


_ONCE_RE = re.compile(r"이번(?:만|에는|엔)|이 질문(?:만|에는|은)|just (?:this|once)|this time|今回(?:だけ)?|这次|這次", re.I)


def resolve_turn(memo, session_id, msg, default="en"):
    """Decide (target, allowed, skip) for this turn. ``memo`` is a dict the caller keeps
    between turns (in memory or on disk); sticky and last-used languages live in it."""
    key = session_id or "default"
    _sticky = memo.setdefault("sticky", {})
    _last = memo.setdefault("last", {})
    msg = strip_host_notes(msg)
    full = _find_cmd(_FULL_CMD, msg, commands_only=True)
    if full and not _ONCE_RE.search(msg):
        _sticky[key] = full[-1]
    part = set(_find_cmd(_PART_CMD, msg))
    once = bool(full) and bool(_ONCE_RE.search(msg))
    if once:
        _sticky.pop(key, None)
    inst, rest = _instruction_language(msg)
    if inst and inst != (_last.get(key) or default) and _short_latin(msg, rest):
        inst = None   # "continue", "ok", "go on": a short English word does not switch the language
    if full:
        target = full[-1]
    else:
        target = _sticky.get(key) or inst or _last.get(key) or default
    if not once:
        _last[key] = target
    allowed = {target} | part
    # languages of pasted / quoted material are allowed (quoting, translating)
    t = strip_technical(_CODE_RE.sub(" ", rest))
    t = re.sub(r"https?://\S+|\S+[\\/]\S+", " ", t)
    c = _script_counts(t)
    if c["ko"] >= 6: allowed.add("ko")
    if c["ja"] >= 3: allowed.add("ja")
    if c["han"] >= 6 and not c["ja"]: allowed.add("zh")
    for s in _SCRIPT_RANGES:
        if c[s] >= 6:
            allowed.add(detect_language(t) if _LANG_SCRIPT.get(detect_language(t) or "") == s else _SCRIPT_DEFAULT[s])
    if len(re.findall(r"[A-Za-z]{2,}", t)) >= 8:
        allowed.add(_latin_lang(t))
    skip = bool(_ALLOW_ALL_RE.search(msg))
    for d in (_sticky, _last):
        if len(d) > 200:
            for k in list(d)[:-100]:
                d.pop(k, None)
    return target, frozenset(allowed), skip


def _short_latin(msg, rest):
    own = msg.replace(rest, "") if rest else msg
    words = re.findall(r"[A-Za-zÀ-ÿĀ-ž']+", own)
    return (len(words) <= 3 and sum(len(w) for w in words) < 25
            and not any(ch.isalpha() and ord(ch) > 0x24F for ch in own))


# Said right after a progress note (the text between tool calls) came out in another language.
_NOTE_FEEDBACK = {
    "ko": "[출력 규칙] 방금 쓴 진행 설명이 한국어가 아니었다. 다음 진행 설명과 답변은 모두 한국어로 쓴다. "
          "이 대화의 앞선 진행 설명이 다른 언어였어도 따라 하지 않는다.",
    "en": "[Output rule] Your last progress note was not in English. Write every following note and the answer "
          "in English, even if earlier notes in this conversation were not.",
    "ja": "[出力ルール] 直前の進捗メモが日本語ではなかった。以降の進捗メモと回答はすべて日本語で書く。"
          "この会話の以前のメモが他の言語でも真似しない。",
    "zh": "[输出规则] 刚才的进度说明不是中文。之后的进度说明和回答全部用中文写，即使本对话之前的说明用了其他语言也不要模仿。",
}


# Said at the start of a turn whose earlier turns had progress notes in another language.
_NOTE_FEEDBACK_EARLIER = {
    "ko": "[출력 규칙] 이 대화의 앞선 진행 설명 중 한국어가 아닌 것이 있었다. 그것을 따라 하지 말고 "
          "이번 진행 설명과 답변은 모두 한국어로 쓴다.",
    "en": "[Output rule] Some earlier progress notes in this conversation were not in English. Do not copy "
          "them; write every note and the answer of this turn in English.",
    "ja": "[出力ルール] この会話の以前の進捗メモに日本語でないものがあった。真似せず、"
          "今回の進捗メモと回答はすべて日本語で書く。",
    "zh": "[输出规则] 本对话之前的进度说明有些不是中文。不要模仿，本轮的进度说明和回答全部用中文写。",
}


def note_feedback(target, earlier=False):
    """The correction added after a progress note written in the wrong language
    (``earlier``: the notes of earlier turns, said once at the start of a turn)."""
    table = _NOTE_FEEDBACK_EARLIER if earlier else _NOTE_FEEDBACK
    if target in table:
        return table[target]
    name = LANG_NAMES.get(target, target)
    if earlier:
        return (f"[Output rule] Some earlier progress notes in this conversation were not in {name}. Do not "
                f"copy them; write every note and the answer of this turn in {name}.")
    return (f"[Output rule] Your last progress note was not in {name}. Write every following note and the "
            f"answer in {name}, even if earlier notes in this conversation were not.")


def note_off_language(text, target, allowed=frozenset()):
    """A progress note (short text before a tool call) written in another language."""
    return wrong_language(text, target, allowed, min_letters=12)
