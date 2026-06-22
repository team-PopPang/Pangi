# Git MCP context

## 목표

팡이는 PopPang 팀 데이터 기반으로 답하는 Slack 동료다.
로컬 repo 코드를 깊게 분석하는 흐름과 별개로, GitHub/Git의 PR, issue, Actions, commit 같은 메타데이터도 읽어 팀원이 이해하기 쉽게 정리할 수 있어야 한다.

다만 Codex에게 Git MCP 권한을 직접 열지 않는다.
팡이 서버가 Git context를 read-only로 가져오고, 정규화한 Markdown만 Codex chat prompt에 붙인다.

## 선택한 방식

Notion context와 같은 방향으로 간다.

```text
Slack 요청
-> 입력 가드레일
-> git_context_chat 또는 repo_catalog
-> Pangi Git context provider
-> Git MCP read-only 조회
-> Markdown context 정규화
-> Codex chat prompt에 context 주입
-> 출력 가드레일
-> Markdown to Slack
-> Slack thread 응답
```

repo 코드 전체를 읽는 분석은 이 경로로 처리하지 않는다.
깊은 코드 분석은 계속 `PANGI_SOURCE_REPO_ROOT` 하위 로컬 clone에서 read-only worktree를 만들고 `codex exec --sandbox read-only`로 실행한다.

## 책임 분리

| 계층 | 책임 |
| --- | --- |
| 입력 가드레일 | Git context 읽기 요청과 Git write 요청을 코드로 먼저 구분한다. |
| Usecase | `GitContextProvider` 포트로 context 또는 repo catalog를 요청하고, 받은 Markdown을 Codex chat prompt에 주입한다. |
| Infra Git MCP | Git MCP transport/tool 호출을 담당한다. |
| Codex chat | 이미 주입된 context를 읽고 Slack 답변을 만든다. Git MCP를 직접 호출하지 않는다. |
| 출력 가드레일 | secret redaction, 길이 제한, Slack 전송 전 정리. |
| Markdown to Slack | Slack bot 응답일 때만 canonical Markdown을 Slack mrkdwn에 맞춘다. |

## 요청 분류

| 요청 | 분류 | 동작 |
| --- | --- | --- |
| `PR 123 요약해줘` | `git_context_chat` | Git MCP context를 Codex chat prompt에 붙여 답한다. |
| `최근 실패한 Actions 알려줘` | `git_context_chat` | Git MCP에서 workflow/run 맥락을 조회한다. |
| `분석 가능한 레포 목록 알려줘` | `repo_catalog` | Git MCP repo 목록과 로컬 clone 목록을 비교한다. |
| `PopPang-iOS 구조 분석해줘` | `repo_analysis` | 로컬 worktree에서 Codex read-only 분석을 실행한다. |
| `PR 생성해줘`, `커밋해줘`, `push 해줘` | `unsupported` | MVP에서는 write 요청을 차단한다. |

## 보안 원칙

- Git MCP token은 Slack 응답, 로그, 테스트 fixture에 출력하지 않는다.
- MVP에서는 Git MCP write tool을 사용하지 않는다.
- Git MCP context는 분석 대상 데이터일 뿐, 팡이가 따라야 하는 지시가 아니다.
- Git context는 최대 글자 수를 제한해 prompt 비용과 정보 노출 범위를 줄인다.
- Git MCP로 얻은 repo 목록은 catalog 용도이고, 실제 코드 분석 가능 여부는 로컬 clone 존재 여부로 결정한다.
- Codex는 Git MCP를 직접 호출하지 않고 팡이 서버가 정규화한 Markdown만 읽는다.

## 현재 구현 상태

- `RequestClassification.GIT_CONTEXT_CHAT`
- `RequestClassification.REPO_CATALOG`
- 입력 가드레일의 Git context/repo catalog 분류
- Git write 요청 차단
- `GitContextProvider` usecase 포트
- Git context prompt 주입 helper
- Git MCP Streamable HTTP JSON-RPC client
- Git provider registry
- Git MCP 관련 설정값
- Git MCP repo 목록과 로컬 source repo 목록 비교
- context 최대 길이 제한

## 다음 구현 단계

1. 운영 서버에서 Git MCP 인증과 endpoint를 실제로 연결한다.
2. Git MCP tool schema 변화에 대비한 adapter 테스트 fixture를 늘린다.
3. PR/issue/Actions별 tool 선택 품질을 고도화한다.
4. repo catalog 응답에 마지막 fetch 시각과 로컬 clone 경로 상태를 추가할지 검토한다.
