# 팡이 안전 규칙

이 문서는 모든 구현 단계에 적용되는 공통 안전 규칙이다.

## 민감 정보

- `.env` 파일을 열람하거나 출력하지 않는다.
- Slack token, signing secret, GitHub token, Notion token, Notion OAuth token store, Codex auth 정보를 문서나 로그에 쓰지 않는다.
- Slack/Notion/GitHub로 보내기 전 secret redaction을 적용한다.

## Slack 접근 제한

- Slack user allowlist를 강제한다.
- Slack channel allowlist를 강제한다.
- bot 자기 자신이 보낸 이벤트는 무시한다.
- Slack retry event로 중복 job을 만들지 않는다.

## Repo 접근 제한

- `PANGI_SOURCE_REPO_ROOT` 하위 repo만 접근 가능하게 제한한다.
- 사용자가 Slack 메시지로 임의 repo path를 지정할 수 없게 한다.
- 원본 source repo에서 Codex를 직접 실행하지 않는다.
- Codex 실행 경로는 서버가 만든 thread workspace여야 한다.
- repo checkout은 thread workspace 하위에서만 만든다.

## Notion 접근 제한

- Notion page/database allowlist를 강제한다.
- MVP에서는 Notion read-only 조회만 허용하고 생성/수정/삭제/기록 요청은 차단한다.
- Codex에는 Notion MCP 권한을 직접 주지 않고, 팡이 서버가 정규화한 Markdown context만 전달한다.

## Git MCP 접근 제한

- Git MCP token은 Slack 응답, 로그, 테스트 fixture에 출력하지 않는다.
- MVP에서는 Git MCP read-only context 조회만 허용하고 PR 생성, issue 생성, commit, push, merge 요청은 차단한다.
- Codex에는 Git MCP 권한을 직접 주지 않고, 팡이 서버가 정규화한 Markdown context만 전달한다.
- Git MCP 조직 repo는 분석 요청 시 `PANGI_SOURCE_REPO_ROOT` 아래로 clone할 수 있다.
- 사용자가 Slack 메시지로 임의 clone URL이나 repo path를 지정할 수 없게 한다.

## Shell 실행 제한

- 사용자 입력을 shell command로 직접 실행하지 않는다.
- `subprocess`에서는 `shell=True`를 사용하지 않는다.
- 외부 명령은 argv list로 구성한다.
- timeout 없는 외부 명령 실행을 만들지 않는다.

## Codex 실행 제한

- 분석은 `--sandbox read-only`만 사용한다.
- 수정은 Slack 승인 이후에만 `--sandbox workspace-write`를 사용한다.
- Codex CLI interactive approval에 의존하지 않고, 승인은 Slack Approval Flow가 담당한다.
- Codex가 commit/push/PR 생성을 직접 하지 않는다.

## Scheduler 제한

- Scheduler는 Codex runner를 직접 호출하지 않고 기존 Slack 요청 처리 usecase를 통과한다.
- 예약 작업 생성 시 Slack user/channel allowlist를 검증한다.
- 예약 prompt도 입력 가드레일과 Orchestrator 정책 보정을 그대로 통과한다.
- `PANGI_SCHEDULER_ENABLED=0`이면 저장된 예약 작업이 있어도 자동 실행하지 않는다.

## Git 안전 규칙

- main/develop 같은 기본 브랜치를 직접 수정하지 않는다.
- repo checkout은 thread workspace 하위 detached worktree로 만든다.
- 같은 Slack thread에는 active Codex session을 최대 1개만 둔다.
- idle timeout을 지난 session은 archive하고 active 연결을 끊는다.
- PR 생성 전 diff와 changed files를 확인한다.
- 허용 범위 밖 파일 변경이 있으면 PR 생성을 막는다.

## 출력 제한

- Slack 메시지가 너무 길면 요약한다.
- stderr와 build log는 필요한 부분만 보여준다.
- Notion에는 redaction된 요약만 저장한다.

## 체크 기준

기능을 만들 때마다 아래를 확인한다.

- secret이 출력되지 않는다.
- timeout이 있다.
- 사용자 입력이 shell로 해석되지 않는다.
- 원본 repo를 직접 수정하지 않는다.
- 실패가 Slack에 보인다.
