# pure-language (한국어 안내)

답변을 **한 가지 언어로만** 유지하는 플러그인입니다. 답변 언어를 정해서 모델에게 미리 알려 주고, 완성된 답변에 다른 언어의 단어나 글자가 섞였는지 검사합니다. 예를 들어 한국어 답변에 끼어든 영어 단어, 일본어 답변에 섞인 중국어 글자, 영어 답변 속 한글을 잡아냅니다.

**헤르메스 에이전트**, **클로드 코드**, **코덱스**에서 쓸 수 있습니다. 표준 파이썬만 쓰므로 따로 설치할 꾸러미가 없습니다.

## 답변 언어를 정하는 순서

1. 메시지에 명시적 명령이 있으면 그 언어로 정하고, 대화가 끝날 때까지 유지합니다. 예: `영어로 답해줘`, `answer in English`. `이번에는`처럼 한 번만이라는 표현이 있으면 그 턴에만 적용합니다.
2. 명령이 없으면 사용자가 쓴 문장의 언어를 따릅니다. 붙여넣은 인용문과 코드는 빼고 판단합니다.
3. 너무 짧은 메시지는 직전 언어를 유지하고, 그것도 없으면 `default_language` 설정을 따릅니다(기본값 `en`).

`일본어로 메일 초안 써줘`처럼 일부만 외국어인 요청은 답변 언어를 그대로 두고 그 언어를 허용합니다. 54개 언어, 22가지 문자를 알아봅니다.

## 도구별 동작

| | 헤르메스 | 클로드 코드 | 코덱스 |
|---|---|---|---|
| 답변 전 경고문 넣기 | 됨 | 됨 | 됨 |
| 완성된 답변 검사 | 됨 | 됨 | 됨 |
| 섞임 처리 | 답변을 교정본으로 **바꿔치기** | 섞인 문장만 짧게 다시 쓰게 함 | 클로드 코드와 같음 |

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
```

## 설정

설정 파일은 선택 사항입니다. 헤르메스는 `$HERMES_HOME/pure-language.json`, 클로드 코드는 `~/.claude/pure-language.json`, 코덱스는 `$CODEX_HOME/pure-language.json`을 읽습니다. 한국어 사용자라면 아래처럼 두는 것을 권합니다.

```json
{ "default_language": "ko" }
```

나머지 항목과 알려야 할 동작(모델 추가 호출, 로컬 상태 파일, 선택 기록)은 [영어 안내](README.md)에 있습니다.
