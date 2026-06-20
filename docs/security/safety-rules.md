# 팡이 안전 규칙

이 문서는 모든 구현 단계에 적용되는 공통 안전 규칙이다.

## 민감 정보

- `.env` 파일을 열람하거나 출력하지 않는다.
- Slack token, signing secret, GitHub token, Notion token, Codex auth 정보를 문서나 로그에 쓰지 않는다.
- Slack/Notion/GitHub로 보내기 전 secret redaction을 적용한다.

## Slack 접근 제한

- Slack user allowlist를 강제한다.
- Slack channel allowlist를 강제한다.
- bot 자기 자신이 보낸 이벤트는 무시한다.
- Slack retry event로 중복 job을 만들지 않는다.

## Repo 접근 제한

- repo allowlist를 강제한다.
- 사용자가 Slack 메시지로 임의 repo path를 지정할 수 없게 한다.
- 원본 source repo에서 Codex를 직접 실행하지 않는다.
- Codex 실행 경로는 서버가 만든 worktree여야 한다.

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

## Git 안전 규칙

- main/develop 같은 기본 브랜치를 직접 수정하지 않는다.
- job마다 별도 worktree와 branch를 만든다.
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
