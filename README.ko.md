# pure-language (한국어 안내)

답변을 **한 가지 언어로만** 유지하는 플러그인입니다. 답변 언어를 정해서 모델에게 미리 알려 주고, 완성된 답변에 다른 언어의 단어나 글자가 섞였는지 검사합니다. 예를 들어 한국어 답변에 끼어든 영어 단어, 일본어 답변에 섞인 중국어 글자, 영어 답변 속 한글을 잡아냅니다.

**헤르메스 에이전트**, **클로드 코드**, **코덱스**, **오픈클로**, **파이**, **오 마이 파이**에서 쓸 수 있습니다. 파이썬 엔진은 표준 라이브러리만, 자바스크립트 엔진(오픈클로, 파이, 오 마이 파이용)은 외부 꾸러미 없이 동작합니다. 두 엔진은 같은 단어표를 읽고, 같은 결과를 내는지 시험으로 확인합니다(`tests/parity.py`).

## 답변 언어를 정하는 순서

1. 메시지에 명시적 명령이 있으면 그 언어로 정하고, 대화가 끝날 때까지 유지합니다. 예: `영어로 답해줘`, `answer in English`. `이번에는`처럼 한 번만이라는 표현이 있으면 그 턴에만 적용합니다.
2. 명령이 없으면 사용자가 쓴 문장의 언어를 따릅니다. 붙여넣은 인용문과 코드는 빼고 판단합니다.
3. 너무 짧은 메시지는 직전 언어를 유지하고, 그것도 없으면 `default_language` 설정을 따릅니다(기본값 `en`).

붙여넣은 명령 창 내용, 기록, 오류 추적(`PS C:\>`, `$`, `kd>` 같은 입력 표시, 색상 부호, 시각 표시 등)은 언어 판단에서 빼고, 답변 속 그런 줄도 고치지 않습니다. 이번 턴에 실행한 명령이나 도구의 출력을 그대로 옮겨 적는 것도 허용합니다. 다만 세 단어 이상(한중일 글자는 열 자 이상)이거나 기술 문자열일 때만 인용으로 보므로, 출력에서 단어 하나만 가져와 섞으면 여전히 섞임으로 잡습니다.

명령 입력 줄(`PS C:\> wpr -status`) 뒤에 이어지는 줄은 다른 문자로 된 줄이나 질문이 나올 때까지 명령 출력으로 봅니다. 그래서 출력만 붙여넣은 메시지를 받아도 답변 언어가 영어로 바뀌지 않습니다.

답변 전체가 다른 언어로 쓰였으면(한국어 대화에 영어 답변 등) 헤르메스는 문장 줄을 한 번에 모두 다시 씁니다. 코드와 목록 구조는 유지하고, 실패하면 답변을 건드리지 않습니다. 클로드 코드와 코덱스는 같은 답을 올바른 언어로 다시 쓰게 합니다.

`일본어로 메일 초안 써줘`처럼 일부만 외국어인 요청은 답변 언어를 그대로 두고 그 언어를 허용합니다. 54개 언어, 22가지 문자를 알아봅니다.

언어 지정은 요청일 때만 명령으로 봅니다. `왜 영어로 대답했지?`처럼 지난 답변을 묻는 말은 언어를 바꾸지 않습니다. 도구가 메시지 끝에 덧붙이는 안내문(`[System: ...]`)과 이 플러그인이 넣은 규칙 줄도 언어 판단에서 뺍니다.

## 지시문 문구

지시문은 외국어 용어를 한국어로 옮겨 쓰고, 원문이 꼭 필요할 때만 처음 한 번 괄호 안 백틱으로 덧붙이라고 요구합니다. 예: 내정 취소(`内定取消し`). 예전 문구("외국어 원문은 백틱 안에 쓴다")는 모델이 원문을 번역하지 않고 백틱에 넣어 그대로 쓰게 만들었습니다. 붙여넣은 자료의 언어는 인용으로만 허용하고 지시문에 "허용"이라고 알리지 않습니다. 알리면 모델이 그 언어로 써도 된다고 받아들이기 때문입니다.

## 도구별 동작

| 도구 | 답변 전 지시문 | 섞인 답변 처리 |
|---|---|---|
| 헤르메스 | `pre_llm_call` | 다시 쓴 답변으로 **바꿔치기** |
| 오 마이 파이 | 숨은 메시지(`before_agent_start`) | 완성된 답변을 **바꿔치기**(`assistant_message`), 글이 흘러나오는 동안에는 섞인 단어를 화면에서 가림 |
| 파이 | 숨은 메시지(`before_agent_start`) | 완성된 답변을 **바꿔치기**(`message_end`), 끝나면 화면도 교정본으로 다시 그림 |
| 오픈클로 | `before_prompt_build` | 초안을 돌려보내 모델이 답변을 다시 쓰게 함(`before_agent_finalize`), 나가는 답변도 교정 |
| 클로드 코드, 코덱스 | `UserPromptSubmit` | 섞인 문장만 다시 쓰게 함(`Stop`) |

섞인 줄은 기본적으로 **모델이 다시 씁니다**. 단어표와 한자 독음으로 고치면 문장이 어색해질 수 있어서, 모델 호출이 실패했을 때만 씁니다. 비용을 줄이려면 `"local_fix": "first"`로 규칙 교정을 먼저 쓰게 할 수 있습니다.

### 도구별 한계

- **파이**: 답변 글자를 그리는 연결 지점이 없어서, 글이 흘러나오는 동안에는 섞인 단어가 잠깐 보입니다. 답변이 끝나면 교정본으로 바뀝니다.
- **오 마이 파이**: 흘러나오는 글을 가리려면 `omp config set extensionRenderers.assistantText true`를 켜야 합니다.
- **오픈클로를 클로드 코드 로그인으로 쓸 때**(`claude-cli` 실행 방식): 답변 확정 직전과 기록 저장 단계에 연결 지점이 없어서, 지시문과 나가는 답변만 처리합니다. 저장된 기록에는 원래 답변이 남습니다. 키로 연결한 모델은 전 과정을 처리합니다.
- **윈도우의 오픈클로**: 실행 중인 게이트웨이에서 플러그인을 다시 읽으면 권한 오류(`EPERM`)가 날 수 있습니다. 게이트웨이를 다시 시작하면 됩니다.

## 설치

```bash
# 헤르메스
hermes plugins install joowon-jang/pure-language
hermes plugins enable pure-language

# 클로드 코드
claude plugin marketplace add joowon-jang/pure-language
claude plugin install pure-language@pure-language

# 코덱스 (설치 후 /hooks 에서 두 연결 지점을 신뢰 허용해야 동작)
codex plugin marketplace add joowon-jang/pure-language
codex plugin add pure-language@pure-language

# 오 마이 파이
omp plugin install github:joowon-jang/pure-language
omp config set extensionRenderers.assistantText true

# 파이
pi install git:github.com/joowon-jang/pure-language

# 오픈클로
openclaw plugins install git:github.com/joowon-jang/pure-language --force --accept-capabilities
openclaw config set plugins.entries.pure-language.hooks.allowConversationAccess true
```

## 비용 줄이기

- 매 턴 붙이는 지시문은 한 줄(40~100자)입니다. 긴 지시문(약 300자)은 섞임이 발견된 다음 턴에만 씁니다.
- 깨끗한 답변은 모델을 호출하지 않습니다.
- 다시 쓰게 할 때는 섞인 줄(긴 줄은 섞인 문장)만 보냅니다. 결과는 저장해 두고 다시 씁니다.
- 다시 쓰기 모델은 작은 모델이면 충분합니다. 시험에서는 `claude-haiku-4-5`로 충분했습니다.
- 비용을 더 줄이려면 `"local_fix": "first"`로 두고 `stop_min_hits`를 높입니다.

## 설정

설정 파일은 선택 사항입니다.

| 도구 | 파일 |
|---|---|
| 헤르메스 | `$HERMES_HOME/pure-language.json` |
| 클로드 코드 | `~/.claude/pure-language.json` |
| 코덱스 | `$CODEX_HOME/pure-language.json` |
| 오 마이 파이 | `~/.omp/agent/pure-language.json` |
| 파이 | `~/.pi/agent/pure-language.json` |
| 오픈클로 | `openclaw.json`의 `plugins.entries.pure-language.config`, 또는 `~/.openclaw/pure-language.json` |

한국어 사용자라면 아래처럼 두는 것을 권합니다.

```json
{ "default_language": "ko" }
```

파이와 오 마이 파이는 `rewrite_model`(`제공자/모델` 형식)로 다시 쓰기 모델을 정할 수 있습니다. 비워 두면 오 마이 파이는 `@smol` 역할 모델, 그다음 대화 중인 모델을 씁니다.

## 다시 쓰기 모델 고르기 (헤르메스)

헤르메스에서 섞인 문장을 고쳐 쓸 때는 플러그인 전용 보조 모델 칸 `pure_language_rewrite`를 씁니다. 따로 고르지 않으면 평소 쓰는 기본 모델을 그대로 씁니다. 이 일은 작은 모델로도 충분하므로, 싼 모델을 지정하면 비용을 줄일 수 있습니다.

**화면에서 고르기**

1. 설정의 모델 화면을 엽니다.
2. **Show auxiliary**를 눌러 보조 작업 목록을 펼칩니다.
3. `Language rewrite` 줄의 **Change**를 누르고 공급자와 모델을 고른 뒤 **Switch**를 누릅니다.

**명령으로 고르기**

```bash
hermes config set auxiliary.pure_language_rewrite.provider anthropic
hermes config set auxiliary.pure_language_rewrite.model claude-haiku-4-5
```

"알 수 없는 설정 키"라는 경고가 나와도 값은 저장되고 동작합니다. 기본 모델로 되돌리려면 `config.yaml`에서 `auxiliary.pure_language_rewrite` 항목을 지우면 됩니다.

다시 쓰기 호출은 최대 `timeout`초(기본 15초)만 기다립니다. 헤르메스는 30초가 넘는 플러그인을 끊고 60초 동안 건너뛰기 때문에, 그 전에 멈추고 규칙으로 고친 결과를 내보냅니다. 모델 호출이 실패하거나 시간을 넘기면 10분 동안은 모델을 부르지 않고 규칙으로만 고칩니다.

클로드 코드와 코덱스는 답변을 바꿔치기할 수 없습니다. 그래서 대화 중인 모델에게 직접 다시 쓰게 하고, 고를 다시 쓰기 모델이 따로 없습니다.

나머지 항목과 알려야 할 동작(모델 추가 호출, 로컬 상태 파일, 선택 기록)은 [영어 안내](README.md)에 있습니다.
