# 팡이 플랫폼 Python 기반 설계 문서

> 이 문서는 긴 설계 초안 보관본이다. 실제 구현 기준은 `AGENTS.md`, `docs/mvp/overview.md`, `docs/implementation-checklist.md`, `docs/architecture/`, `docs/security/safety-rules.md`를 우선한다.

## 0. 방향성 요약

팡이는 PopPang 팀 전용 Slack 기반 개발 에이전트 플랫폼이다. 팀원이 Slack에서 `@팡이` 또는 slash command로 요청하면 서버가 요청을 받고, Orchestrator가 작업을 분류한 뒤, 필요한 경우 격리된 git worktree에서 `codex exec`를 실행한다. 결과는 Slack thread에 요약하고, Notion에는 episode report로 남기며, 승인된 경우에만 GitHub PR을 생성한다.

현재 저장소의 `poppangbot/`은 Slack 연결을 검증하기 위한 FastAPI 샘플로 본다. 실제 플랫폼은 이 샘플을 참고하되, `poppangbot` 내부를 무리하게 키우기보다 별도 Python 서버 패키지로 분리하는 것을 권장한다.

추천 기술 스택:

- Web server: FastAPI
- Async runtime: asyncio
- Job queue: 초기 MVP는 asyncio background task, 이후 Redis + RQ 또는 Celery
- DB: PostgreSQL
- ORM/query: SQLAlchemy 2.x 또는 SQLModel
- Migration: Alembic
- Slack: slack_sdk
- Notion: notion-client
- GitHub: PyGithub 또는 GitHub CLI 래핑
- Codex 실행: Python `asyncio.create_subprocess_exec`
- Log/trace: structlog + JSON log

Python 기반을 추천하는 이유:

- 기존 `poppangbot`이 FastAPI로 되어 있어 Slack signature 검증, event route, 테스트 구조를 재사용하기 쉽다.
- `codex exec`, `git`, `xcodebuild` 같은 외부 프로세스 제어는 Python에서 안정적으로 다루기 좋다.
- MVP 단계에서는 TypeScript보다 구성 요소가 적고, 운영 스크립트와 서버 로직을 같은 언어로 묶기 쉽다.
- 단, 대규모 queue/worker 운영으로 커질 경우에는 worker 분리, timeout, cancellation, log streaming 구조를 초기에 잘 잡아야 한다.

## 1. 전체 아키텍처

```text
Slack Mention / Slash Command
-> Webhook Server(FastAPI)
-> Orchestrator
-> Job Queue
-> Git Worktree Manager
-> Codex Runner
-> stdout/stderr/diff/test log Collector
-> Slack Thread Reply
-> Notion Episode Report
-> GitHub PR Manager(optional)
```

### Slack Bot

역할:

- Slack slash command와 app mention을 받는다.
- Slack request signature를 검증한다.
- 사용자의 Slack user/channel/team 정보를 추출한다.
- 긴 작업은 3초 안에 접수 메시지를 반환하고, 결과는 thread에 후속 응답한다.
- 승인 버튼, 취소 버튼, PR 생성 버튼 같은 interactive action을 처리한다.

초기 구현은 `poppangbot/app.py`의 request signature 검증 방식을 참고한다.

### Webhook Server

역할:

- FastAPI route를 제공한다.
- `/slack/events`, `/slack/commands`, `/slack/interactions`, `/health`를 담당한다.
- Slack 요청을 내부 command 객체로 정규화한다.
- allowlist, idempotency, retry 방지를 먼저 처리한다.

권장:

- Slack retry header(`X-Slack-Retry-Num`)를 감지해 중복 job 생성을 막는다.
- Slack `event_id`, `team_id`, `channel`, `thread_ts`를 idempotency key로 사용한다.

### Orchestrator

역할:

- 요청을 분석하여 job type을 분류한다.
- 실행 모드(read-only, workspace-write, PR summary, troubleshooting)를 결정한다.
- 승인 필요 여부를 판단한다.
- Codex prompt template을 선택한다.
- Codex 실행 후 결과를 Slack/Notion/GitHub 단계로 넘긴다.

분류 예시:

- 분석만 요청: `ANALYZE`
- 코드 수정 요청: `EDIT_REQUESTED`, 단 첫 실행은 `ANALYZE`
- PR 생성 요청: `PR_CREATE_REQUESTED`, diff와 테스트 결과 확인 후 승인 필요
- PR 요약 요청: `PR_SUMMARY`
- 빌드 실패 분석: `XCODEBUILD_FAILURE`
- 이전 thread 이어서 요청: 기존 `slack_threads`와 `codex_sessions` 조회

### Job Queue

역할:

- Slack 요청을 비동기 job으로 실행한다.
- 동시에 여러 요청이 들어와도 repo별/사용자별 concurrency를 제한한다.
- timeout, retry, cancellation을 관리한다.

MVP:

- FastAPI `BackgroundTasks` 또는 `asyncio.create_task`
- 단일 서버, 단일 worker

운영형:

- Redis + RQ 또는 Celery
- repo별 queue lock
- job 상태 DB 저장

### Codex Runner

역할:

- `codex exec` 명령을 실행한다.
- sandbox, approval mode, working directory, prompt를 안전하게 구성한다.
- stdout, stderr, exit code, duration을 수집한다.
- timeout 발생 시 프로세스를 종료하고 Slack/Notion에 실패 기록을 남긴다.

핵심 원칙:

- 사용자가 보낸 원문을 shell string으로 직접 조립하지 않는다.
- `subprocess`는 `shell=False`로 실행한다.
- prompt는 argv의 마지막 인자로 넘기거나 임시 prompt 파일을 읽어 구성한다.
- Codex가 직접 commit/push하지 않고, 서버가 diff 확인 후 승인 기반으로 git 작업을 수행한다.

### Git Worktree Manager

역할:

- job마다 원본 repo를 직접 수정하지 않도록 격리된 worktree를 만든다.
- base branch를 checkout하고, job branch를 생성한다.
- 작업 완료 후 diff, changed files, test log를 수집한다.
- cleanup 정책에 따라 오래된 worktree를 정리한다.

권장 경로:

```text
/repos/sources/PopPang-iOS
/repos/worktrees/{job_id}
```

### Notion Reporter

역할:

- 작업 결과를 episode report 형태로 저장한다.
- 요청자, Slack thread, job type, 요약, 근거 파일, diff 요약, 테스트 결과, PR URL을 기록한다.
- secret이 포함된 로그는 redaction 후 저장한다.

### GitHub PR Manager

역할:

- 승인된 작업만 commit/push/PR 생성한다.
- PR 제목/본문을 생성한다.
- PR 생성 전 diff와 테스트 결과를 Slack thread에 확인시킨다.

권장:

- Codex에게 git commit/push 권한을 주지 않는다.
- 서버가 git 상태와 승인 흐름을 통제한다.

### DB/Storage

역할:

- Slack thread와 job 상태를 연결한다.
- Codex session, prompt, stdout, stderr, diff, test log를 저장한다.
- 승인 상태, PR URL, Notion page id를 추적한다.

대용량 로그:

- MVP는 PostgreSQL text 컬럼
- 운영형은 S3 호환 object storage에 원본 로그 저장 후 DB에는 URI 저장

### Logger/Trace Collector

역할:

- job 단위 trace id를 생성한다.
- route, orchestrator, codex run, git, notion, github 단계를 JSON log로 남긴다.
- secret redaction을 공통 적용한다.

### Approval Manager

역할:

- workspace-write 실행 승인
- PR 생성 승인
- 실패 후 재시도 승인
- 작업 취소 처리

승인 정책:

- read-only 분석은 allowlist 통과 시 자동 실행 가능
- workspace-write는 Slack 버튼 승인 필요
- PR 생성은 diff/test 결과 확인 후 별도 승인 필요

## 2. 요청 처리 흐름

### 분석만 요청

입력 예시:

```text
@팡이 LoginView가 너무 복잡한데 구조 한번 분석해줘
```

Orchestrator 판단:

- job type: `ANALYZE`
- mode: read-only
- approval: 불필요

실행 단계:

1. Slack event 수신
2. allowlist 확인
3. thread/job 생성
4. worktree 생성
5. `codex exec --sandbox read-only` 실행
6. stdout 요약
7. Slack thread 응답
8. Notion report 저장

Codex 실행 여부:

- 실행함

Slack 응답 예시:

```text
팡이가 먼저 읽어봤습니다.

결론: LoginView는 화면 상태, 인증 요청, 에러 표시 책임이 한 파일에 몰려 있습니다.
근거 파일:
- Sources/Auth/LoginView.swift
- Sources/Auth/AuthViewModel.swift

추천:
1. View state 분리
2. 인증 요청 로직 ViewModel 유지
3. 에러 매핑은 별도 helper로 이동
```

Notion 기록 여부:

- 기록함

실패 시 처리:

- Codex exit code, stderr 요약
- 재시도 버튼 제공

### 코드 수정 요청

입력 예시:

```text
@팡이 LoginView 리팩터링해줘
```

Orchestrator 판단:

- job type: `EDIT_REQUESTED`
- 첫 단계 mode: read-only
- approval: 분석 후 수정 승인 필요

실행 단계:

1. read-only 분석 실행
2. 영향 범위와 수정 계획 Slack thread에 반환
3. `수정 실행` 버튼 표시
4. 승인 시 workspace-write mode로 Codex 실행
5. diff 수집
6. 테스트 또는 build 실행
7. diff/test 결과 Slack 반환
8. PR 생성 승인 대기

Codex 실행 여부:

- read-only 1회
- 승인 후 workspace-write 1회

Slack 응답 예시:

```text
수정 전에 먼저 봤습니다.

수정 후보:
- Sources/Auth/LoginView.swift
- Sources/Auth/LoginViewModel.swift

위 범위로 worktree에서만 수정해도 될까요?
[수정 실행] [취소]
```

Notion 기록 여부:

- 분석 단계 기록
- 수정 단계 완료 후 업데이트

실패 시 처리:

- 수정 실패 시 diff를 폐기하지 않고 worktree 유지
- Slack에 실패 요약과 로그 일부 제공

### PR 생성 요청

입력 예시:

```text
@팡이 이 변경사항 PR로 올려줘
```

Orchestrator 판단:

- 기존 job diff가 있는지 확인
- 승인 상태 확인
- 테스트 결과 확인

실행 단계:

1. thread에서 최근 completed edit job 조회
2. diff/test log 요약
3. PR 생성 승인 요청
4. 승인 시 서버가 commit/push
5. GitHub PR 생성
6. Slack/Notion에 PR URL 기록

Codex 실행 여부:

- PR 본문 보강이 필요하면 read-only 또는 prompt-only Codex 실행 가능
- git push/PR 생성은 서버 담당

Slack 응답 예시:

```text
PR 생성 전 확인입니다.

변경 파일 3개, 테스트 결과: 통과
브랜치: pangi/job-20260617-login-refactor

[PR 생성] [취소]
```

Notion 기록 여부:

- PR URL 포함해 업데이트

실패 시 처리:

- push 실패, PR 생성 실패를 구분해 Slack에 반환

### PR 요약 요청

입력 예시:

```text
@팡이 https://github.com/PopPang/PopPang-iOS/pull/123 요약해줘
```

Orchestrator 판단:

- job type: `PR_SUMMARY`
- mode: read-only

실행 단계:

1. PR URL 파싱
2. GitHub API 또는 gh CLI로 PR diff/metadata 수집
3. Codex에 요약 prompt 전달
4. Slack thread 응답
5. Notion 기록 선택

Codex 실행 여부:

- 실행함

Slack 응답 예시:

```text
PR #123 요약입니다.

결론: 로그인 실패 처리 UX를 개선하는 PR입니다.
주요 변경:
- AuthError 표시 문구 정리
- LoginViewModel 테스트 추가
리뷰 포인트:
- 네트워크 실패와 인증 실패 메시지가 의도대로 분리되는지 확인
```

Notion 기록 여부:

- 기본 기록함

실패 시 처리:

- GitHub 권한/PR 접근 실패를 명확히 반환

### 트러블슈팅 문서화 요청

입력 예시:

```text
@팡이 오늘 xcodebuild 실패했던 내용 트러블슈팅 문서로 정리해줘
```

Orchestrator 판단:

- job type: `TROUBLESHOOTING_REPORT`
- mode: read-only

실행 단계:

1. Slack thread 또는 첨부 로그 수집
2. 관련 파일/로그 분석
3. 원인, 재현 조건, 해결 방법, 예방책 작성
4. Slack 요약
5. Notion report 저장

Codex 실행 여부:

- 실행함

Notion 기록 여부:

- 기록함

실패 시 처리:

- 로그 부족 시 필요한 추가 정보 요청

### 이전 Slack thread에서 이어서 요청

입력 예시:

```text
@팡이 아까 말한 2번 방향으로 진행해줘
```

Orchestrator 판단:

- `channel_id + thread_ts`로 기존 thread 조회
- 최근 job과 codex session 조회
- 승인 필요 여부 판단

실행 단계:

1. thread context 조회
2. 이전 결과 요약을 prompt에 포함
3. 필요 시 `codex exec resume` 전략 사용
4. 작업 진행

Codex 실행 여부:

- 이어서 분석 또는 수정 단계에 따라 실행

실패 시 처리:

- 기존 session이 없거나 만료되면 thread summary 기반으로 새 실행

### Codex 실행 실패

처리:

- exit code, stderr, duration 저장
- Slack에 실패 원인 요약
- Notion에 실패 episode 기록
- 재시도 버튼 제공

### Codex 실행 타임아웃

처리:

- 프로세스 terminate 후 kill
- partial stdout/stderr 저장
- Slack에 timeout 안내
- worktree 유지 여부를 정책에 따라 결정

### xcodebuild 실패

처리:

- raw log 저장
- 에러 부분만 추출
- `xcodebuild_failure_prompt`로 Codex 재분석
- Slack에 원인 후보와 수정 후보 반환

### 사용자가 승인하지 않은 경우

처리:

- job status를 `rejected`로 변경
- worktree cleanup 예약
- Notion에는 "승인 거절"로 기록

### 동시에 여러 요청이 들어온 경우

처리:

- user/channel allowlist 확인
- repo별 concurrency limit 적용
- 같은 Slack event id는 중복 job 생성 금지
- 오래 걸리는 작업은 queue position 안내

## 3. Codex CLI 실행 전략

### read-only 분석 모드

```bash
codex exec \
  -C /repos/worktrees/{job_id} \
  --sandbox read-only \
  --ask-for-approval never \
  "{prompt}"
```

용도:

- 코드 분석
- PR 요약
- 실패 로그 분석
- 수정 전 계획 수립

### workspace-write 수정 모드

```bash
codex exec \
  -C /repos/worktrees/{job_id} \
  --sandbox workspace-write \
  --ask-for-approval never \
  "{prompt}"
```

용도:

- 승인된 수정 작업
- 테스트 파일 추가
- 문서 수정

주의:

- 반드시 worktree 경로에서만 실행한다.
- main/develop 원본 repo에서 실행하지 않는다.
- 승인 없이 실행하지 않는다.

### PR 생성 전 diff 확인

```bash
git -C /repos/worktrees/{job_id} status --short
git -C /repos/worktrees/{job_id} diff --stat
git -C /repos/worktrees/{job_id} diff -- . ':!*.xcuserstate'
```

서버는 이 결과를 요약해 Slack에 보여준 뒤 PR 생성 승인을 받는다.

### session resume 전략

권장 단계:

1. MVP에서는 thread context와 이전 stdout 요약을 DB에서 불러와 새 `codex exec` prompt에 포함한다.
2. Codex CLI가 안정적인 session resume 옵션을 제공하는 환경에서는 `codex_sessions.session_id`를 저장하고 이어서 실행한다.
3. session resume 실패 시 새 실행으로 fallback한다.

예시:

```bash
codex exec resume {session_id} \
  -C /repos/worktrees/{job_id} \
  --sandbox read-only \
  --ask-for-approval never \
  "{prompt}"
```

### stdout/stderr/diff/test log 수집

수집 항목:

- `stdout`
- `stderr`
- `exit_code`
- `started_at`
- `finished_at`
- `duration_ms`
- `git status --short`
- `git diff --stat`
- `git diff`
- test/build log

저장 정책:

- Slack에는 요약과 핵심 로그만 표시
- Notion에는 redaction된 요약 저장
- 원본 로그는 DB 또는 object storage에 저장

### `--skip-git-repo-check`를 쓰면 안 되는 상황

금지:

- worktree 경로가 검증되지 않았을 때
- repo allowlist에 없는 경로일 때
- 임시 디렉터리나 사용자가 지정한 임의 경로일 때
- main/develop 원본 repo에서 실행할 가능성이 있을 때

허용 검토:

- 서버가 생성한 worktree이고
- repo allowlist를 통과했고
- branch/worktree metadata가 DB와 일치하며
- 별도 sandbox 정책이 적용된 경우

기본값은 사용하지 않는 것이다.

### sandbox/approval 권장값

분석:

```text
--sandbox read-only
--ask-for-approval never
```

수정:

```text
--sandbox workspace-write
--ask-for-approval never
```

이유:

- Slack 기반 서버 환경에서는 Codex CLI의 interactive approval을 쓰기 어렵다.
- 승인은 Slack Approval Manager가 담당한다.

### 긴 작업 timeout

권장 기본값:

- 분석: 10분
- 수정: 20분
- xcodebuild build: 30분
- xcodebuild test: 45분

timeout 시:

- Codex 프로세스 종료
- partial log 저장
- Slack thread에 실패 안내
- 재시도/취소 버튼 제공

### Codex 실행 중 Slack 중간 상태

예시:

```text
팡이가 worktree를 만들고 있습니다.
팡이가 read-only로 코드를 읽고 있습니다.
분석이 길어지고 있어요. 현재 6분째 진행 중입니다.
분석은 완료됐고, 결과를 정리하고 있습니다.
```

구현:

- job 상태가 바뀔 때 Slack `chat.postMessage`
- 같은 thread에만 작성
- 너무 자주 쓰지 않도록 최소 30~60초 간격 제한

### Codex가 수정한 파일 감지

```bash
git -C /repos/worktrees/{job_id} status --porcelain=v1
git -C /repos/worktrees/{job_id} diff --name-only
```

서버는 changed files를 DB에 저장하고, 허용 경로 밖 변경이 있으면 PR 생성을 막는다.

### commit/push 주체 권장안

권장:

- Codex: 코드 분석/수정만 담당
- 서버: diff 검증, commit, push, PR 생성 담당

이유:

- 승인 흐름을 서버가 통제할 수 있다.
- git credential 노출 범위를 줄일 수 있다.
- PR 생성 전 정책 검사를 강제할 수 있다.

## 4. Prompt Template 설계

### 공통 규칙

모든 prompt에 포함한다.

```text
너는 PopPang 팀의 개발 에이전트 "팡이"다.
결론을 먼저 말하고, 근거 파일 경로를 반드시 표시한다.
근거 없는 추측은 하지 않는다.
확인한 사실과 추정을 분리한다.
파일 수정 가능 여부를 반드시 따른다.
변경 이유와 영향 범위를 작성한다.
검증 방법을 작성한다.
마지막에는 "요약" 섹션을 작성한다.
PopPang 팀이 빠르게 판단할 수 있도록 짧고 명확하게 답한다.
```

### analyze_prompt

```text
[모드] read-only 분석

파일을 수정하지 마라.
사용자 요청:
{user_request}

대상 repo:
{repo_name}

관련 Slack thread 요약:
{thread_summary}

해야 할 일:
1. 관련 파일을 찾아 읽어라.
2. 문제의 실제 원인을 분석하라.
3. 확인한 파일 경로를 표시하라.
4. 수정이 필요하다면 수정 계획만 제안하라.
5. 검증 방법을 제안하라.

출력 형식:
## 결론
## 확인한 근거
## 영향 범위
## 추천 작업
## 검증 방법
## 요약
```

### edit_prompt

```text
[모드] workspace-write 수정

이 작업은 Slack에서 승인되었다.
허용된 worktree 안에서만 파일을 수정하라.
사용자 요청:
{user_request}

승인된 수정 계획:
{approved_plan}

제약:
- 승인된 범위 밖 파일은 수정하지 마라.
- secret, token, .env 파일을 열람하거나 출력하지 마라.
- public API나 DI 구조 변경이 필요하면 먼저 이유를 설명하고 최소 변경으로 처리하라.

해야 할 일:
1. 필요한 파일만 수정하라.
2. 변경 이유를 설명하라.
3. 변경 파일 목록을 출력하라.
4. 가능한 검증 명령을 제안하거나 실행 가능한 테스트를 안내하라.

출력 형식:
## 결론
## 변경 파일
## 변경 내용
## 영향 범위
## 검증 방법
## 요약
```

### pr_summary_prompt

```text
[모드] PR 요약

파일을 수정하지 마라.
PR 정보:
{pr_metadata}

PR diff:
{pr_diff}

해야 할 일:
1. PR의 목적을 한 문장으로 요약하라.
2. 주요 변경 파일과 변경 이유를 정리하라.
3. 리뷰어가 봐야 할 리스크를 정리하라.
4. 테스트/검증 정보가 부족하면 명시하라.

출력 형식:
## 결론
## 주요 변경
## 리뷰 포인트
## 테스트/검증
## 리스크
## 요약
```

### troubleshooting_report_prompt

```text
[모드] 트러블슈팅 문서화

파일을 수정하지 마라.
사용자 요청:
{user_request}

로그/상황:
{logs}

해야 할 일:
1. 증상을 정리하라.
2. 확인된 원인과 추정 원인을 분리하라.
3. 재현 방법을 작성하라.
4. 해결 방법과 예방책을 작성하라.
5. Notion에 옮기기 쉬운 형태로 작성하라.

출력 형식:
## 결론
## 증상
## 원인
## 해결 방법
## 재현/검증
## 예방책
## 요약
```

### xcodebuild_failure_prompt

```text
[모드] xcodebuild 실패 분석

파일을 수정하지 마라.
xcodebuild 명령:
{build_command}

실패 로그:
{build_log_excerpt}

해야 할 일:
1. 첫 번째 의미 있는 에러를 찾아라.
2. 관련 파일 경로와 라인 정보를 표시하라.
3. 빌드 설정 문제와 코드 문제를 구분하라.
4. 수정 후보와 검증 방법을 제안하라.

출력 형식:
## 결론
## 핵심 에러
## 관련 파일
## 원인 후보
## 수정 방향
## 재검증 명령
## 요약
```

## 5. DB 스키마 초안

PostgreSQL 기준 예시다.

```sql
create table slack_threads (
  id uuid primary key,
  team_id text not null,
  channel_id text not null,
  thread_ts text not null,
  root_message_ts text,
  requester_slack_user_id text not null,
  repo text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (team_id, channel_id, thread_ts)
);

create table agent_jobs (
  id uuid primary key,
  slack_thread_id uuid references slack_threads(id),
  job_type text not null,
  status text not null,
  requester_slack_user_id text not null,
  repo text not null,
  base_branch text not null,
  work_branch text,
  prompt text not null,
  summary text,
  error_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table codex_runs (
  id uuid primary key,
  job_id uuid not null references agent_jobs(id),
  mode text not null,
  session_id text,
  command jsonb not null,
  prompt text not null,
  stdout text,
  stderr text,
  exit_code integer,
  timed_out boolean not null default false,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default now()
);

create table worktrees (
  id uuid primary key,
  job_id uuid not null references agent_jobs(id),
  source_repo_path text not null,
  worktree_path text not null,
  base_branch text not null,
  work_branch text not null,
  status text not null,
  created_at timestamptz not null default now(),
  cleaned_at timestamptz
);

create table approvals (
  id uuid primary key,
  job_id uuid not null references agent_jobs(id),
  approval_type text not null,
  status text not null,
  requested_by text not null,
  decided_by text,
  slack_message_ts text,
  created_at timestamptz not null default now(),
  decided_at timestamptz
);

create table job_artifacts (
  id uuid primary key,
  job_id uuid not null references agent_jobs(id),
  artifact_type text not null,
  content text,
  storage_uri text,
  created_at timestamptz not null default now()
);

create table notion_reports (
  id uuid primary key,
  job_id uuid not null references agent_jobs(id),
  notion_page_id text,
  status text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table pull_requests (
  id uuid primary key,
  job_id uuid not null references agent_jobs(id),
  repo text not null,
  branch text not null,
  pr_url text,
  status text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
```

## 6. Python 프로젝트 디렉토리 구조

추천 구조:

```text
pangi/
  pyproject.toml
  README.md
  alembic.ini
  src/
    pangi/
      app.py
      config/
        settings.py
      slack/
        routes.py
        signatures.py
        client.py
        interactions.py
      orchestrator/
        classifier.py
        service.py
        policies.py
      jobs/
        queue.py
        worker.py
        models.py
      codex/
        runner.py
        prompts.py
        parser.py
      git/
        worktrees.py
        diff.py
        github.py
      notion/
        reporter.py
      db/
        session.py
        models.py
        repositories.py
      logger/
        setup.py
        redaction.py
      approvals/
        service.py
      templates/
        analyze.md
        edit.md
        pr_summary.md
        troubleshooting_report.md
        xcodebuild_failure.md
      types/
        enums.py
  tests/
    test_slack_signatures.py
    test_classifier.py
    test_codex_runner.py
    test_worktrees.py
```

주요 파일:

- `app.py`: FastAPI 앱 생성과 route 등록
- `config/settings.py`: 환경변수 로딩, allowlist, repo path 설정
- `slack/routes.py`: Slack events/commands endpoint
- `slack/signatures.py`: Slack request signature 검증
- `orchestrator/classifier.py`: 요청 분류
- `jobs/worker.py`: background job 실행
- `codex/runner.py`: `codex exec` subprocess 실행
- `git/worktrees.py`: git worktree 생성/정리
- `git/diff.py`: diff/status 수집
- `approvals/service.py`: Slack 승인 상태 관리
- `logger/redaction.py`: secret masking

## 7. MVP 구현 순서

### 1차 MVP

목표:

```text
Slack 요청
-> 서버 수신
-> codex exec read-only 실행
-> Slack thread에 분석 결과 반환
```

필요한 파일:

- `slack/routes.py`
- `orchestrator/classifier.py`
- `jobs/worker.py`
- `codex/runner.py`
- `git/worktrees.py`

필요한 외부 API:

- Slack Events API
- Slack Web API `chat.postMessage`

완료 기준:

- `@팡이 분석해줘` 요청이 thread에 결과를 반환한다.
- Codex는 read-only sandbox로만 실행된다.
- job 상태가 DB에 저장된다.

주의할 점:

- Slack 3초 응답 제한
- Codex timeout
- secret redaction

### 2차 MVP

목표:

- Notion episode report 저장 추가

필요한 파일:

- `notion/reporter.py`
- `templates/troubleshooting_report.md`

필요한 외부 API:

- Notion API

완료 기준:

- 분석 결과가 Notion page로 저장된다.
- Slack thread에 Notion URL이 표시된다.

주의할 점:

- Notion에 secret/log 원문을 그대로 올리지 않는다.

### 3차 MVP

목표:

```text
Slack 승인 버튼
-> workspace-write 수정 실행
-> diff 반환
```

필요한 파일:

- `slack/interactions.py`
- `approvals/service.py`
- `codex/runner.py`
- `git/diff.py`

필요한 외부 API:

- Slack Interactivity

완료 기준:

- read-only 분석 후 승인 버튼이 표시된다.
- 승인 시 worktree에서만 수정된다.
- diff summary가 Slack thread에 표시된다.

주의할 점:

- 승인자 allowlist
- 승인된 범위 밖 파일 변경 차단

### 4차 MVP

목표:

- GitHub PR 생성 추가

필요한 파일:

- `git/github.py`
- `approvals/service.py`

필요한 외부 API:

- GitHub API 또는 GitHub CLI

완료 기준:

- diff/test 확인 후 승인 시 PR이 생성된다.
- Slack/Notion에 PR URL이 기록된다.

주의할 점:

- 서버가 commit/push를 담당한다.
- PR 생성 전 main/develop 직접 수정 여부를 검사한다.

### 5차 MVP

목표:

- Slack thread별 Codex session resume 추가

필요한 파일:

- `codex/runner.py`
- `db/models.py`
- `orchestrator/service.py`

완료 기준:

- 같은 Slack thread의 후속 요청이 이전 context를 활용한다.
- session resume 실패 시 DB 요약 기반으로 fallback한다.

주의할 점:

- session_id 저장 포맷은 CLI 버전에 종속될 수 있다.

### 6차 MVP

목표:

- PR 요약
- xcodebuild 실패 분석
- 트러블슈팅 문서화

필요한 파일:

- `templates/pr_summary.md`
- `templates/xcodebuild_failure.md`
- `templates/troubleshooting_report.md`
- `git/github.py`

완료 기준:

- PR URL을 주면 요약한다.
- xcodebuild 실패 로그를 주면 원인 후보를 정리한다.
- Notion 문서로 남긴다.

주의할 점:

- 외부 PR 접근 권한
- 긴 로그 truncation

## 8. 안전장치

필수:

- Slack user allowlist
- Slack channel allowlist
- repo allowlist
- branch allowlist
- prompt injection 방지
- Codex auth 파일 보호
- worktree 격리
- main/develop 직접 수정 금지
- workspace-write 실행 전 승인
- PR 생성 전 승인
- 로그 secret redaction
- Notion secret redaction
- 동시 실행 제한
- job timeout
- 작업 취소
- worktree cleanup
- Codex 실행 권한 제한
- shell command injection 방지
- 사용자가 임의 shell command를 실행시키지 못하게 하는 정책

구체 정책:

- 사용자의 Slack 메시지는 prompt로만 사용하고 shell command로 해석하지 않는다.
- `subprocess`는 항상 `shell=False`로 실행한다.
- repo 경로는 사용자가 직접 지정하지 못하게 하고 서버 allowlist key로만 선택한다.
- `.env`, token, signing key, auth 파일은 Codex prompt에 포함하지 않는다.
- Slack/Notion/GitHub에 출력하기 전 redaction filter를 통과시킨다.
- queue worker는 OS 계정/권한을 분리한다.
- job당 최대 실행 시간과 최대 로그 크기를 제한한다.

## 9. iOS / PopPang-iOS 검증 전략

### xcodebuild build 예시

```bash
xcodebuild \
  -workspace PopPang.xcworkspace \
  -scheme PopPang \
  -sdk iphonesimulator \
  -destination 'platform=iOS Simulator,name=iPhone 16' \
  -derivedDataPath /tmp/pangi-derived-data/{job_id} \
  build
```

### xcodebuild test 예시

```bash
xcodebuild \
  -workspace PopPang.xcworkspace \
  -scheme PopPang \
  -sdk iphonesimulator \
  -destination 'platform=iOS Simulator,name=iPhone 16' \
  -derivedDataPath /tmp/pangi-derived-data/{job_id} \
  test
```

### Tuist 사용 시

generate:

```bash
tuist generate
```

build:

```bash
tuist build PopPang
```

test:

```bash
tuist test PopPang
```

전략:

- repo에 `Project.swift` 또는 `Tuist/`가 있으면 Tuist 우선
- `*.xcworkspace`가 있으면 workspace 기반 xcodebuild
- `*.xcodeproj`만 있으면 project 기반 xcodebuild

### 실패 로그 재분석

1. build/test log를 파일로 저장한다.
2. `error:`, `warning:`, `BUILD FAILED`, Swift compiler error 주변 200~400줄을 추출한다.
3. `xcodebuild_failure_prompt`로 Codex read-only 분석을 실행한다.
4. Slack에는 핵심 원인과 관련 파일만 요약한다.
5. Notion에는 명령어, 실패 요약, 재검증 명령을 기록한다.

### timeout 전략

- generate: 10분
- build: 30분
- test: 45분
- timeout 시 partial log 저장 후 실패 처리

### PR 생성 전 최소 검증 기준

권장 최소:

- `git diff --check`
- 관련 unit test
- 가능한 경우 `xcodebuild build`

시간이 부족할 때:

- build 미실행 사유를 Slack/Notion/PR body에 명시한다.

## 10. 실제 구현 예시 코드

### 핵심 타입 정의

```python
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class JobType(StrEnum):
    ANALYZE = "analyze"
    EDIT_REQUESTED = "edit_requested"
    PR_SUMMARY = "pr_summary"
    TROUBLESHOOTING_REPORT = "troubleshooting_report"
    XCODEBUILD_FAILURE = "xcodebuild_failure"


class CodexMode(StrEnum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"


@dataclass(frozen=True)
class SlackCommand:
    team_id: str
    channel_id: str
    user_id: str
    text: str
    thread_ts: str | None


@dataclass(frozen=True)
class CodexRunResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
```

### Slack event handler 의사코드

```python
from fastapi import APIRouter, Request

router = APIRouter()


@router.post("/slack/events")
async def slack_events(request: Request):
    body = await request.body()
    verify_slack_signature(request.headers, body)
    payload = await request.json()

    if payload.get("type") == "url_verification":
        return {"challenge": payload["challenge"]}

    event = payload.get("event", {})
    if event.get("type") != "app_mention":
        return {"ok": True}

    command = normalize_app_mention(event, payload)
    job = await orchestrator.create_job_from_slack(command)
    await slack_client.reply(command.channel_id, command.thread_ts, f"팡이가 접수했습니다. job={job.id}")
    job_queue.enqueue(job.id)
    return {"ok": True}
```

### Orchestrator classify 예시

```python
def classify_request(text: str) -> JobType:
    lowered = text.lower()

    if "pr" in lowered and ("요약" in text or "리뷰" in text):
        return JobType.PR_SUMMARY
    if "xcodebuild" in lowered or "빌드 실패" in text:
        return JobType.XCODEBUILD_FAILURE
    if "문서" in text or "트러블슈팅" in text:
        return JobType.TROUBLESHOOTING_REPORT
    if "고쳐" in text or "수정" in text or "리팩터링" in text:
        return JobType.EDIT_REQUESTED
    return JobType.ANALYZE
```

### Job 생성 함수 예시

```python
async def create_job_from_slack(command: SlackCommand) -> AgentJob:
    job_type = classify_request(command.text)
    thread = await threads_repo.get_or_create(
        team_id=command.team_id,
        channel_id=command.channel_id,
        thread_ts=command.thread_ts,
        requester_slack_user_id=command.user_id,
    )
    return await jobs_repo.create(
        slack_thread_id=thread.id,
        job_type=job_type,
        status="queued",
        requester_slack_user_id=command.user_id,
        repo="PopPang-iOS",
        base_branch="develop",
        prompt=command.text,
    )
```

### codex exec 실행 함수 예시

```python
import asyncio


async def run_codex_exec(
    *,
    workdir: Path,
    prompt: str,
    mode: CodexMode,
    timeout_seconds: int,
) -> CodexRunResult:
    sandbox = "read-only" if mode == CodexMode.READ_ONLY else "workspace-write"
    args = [
        "codex",
        "exec",
        "-C",
        str(workdir),
        "--sandbox",
        sandbox,
        "--ask-for-approval",
        "never",
        prompt,
    ]

    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
        return CodexRunResult(
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            exit_code=process.returncode or 0,
            timed_out=False,
        )
    except asyncio.TimeoutError:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError:
            process.kill()
        return CodexRunResult(stdout="", stderr="codex exec timed out", exit_code=124, timed_out=True)
```

### git worktree 생성 함수 예시

```python
async def run_command(args: list[str], cwd: Path | None = None, timeout: int = 60) -> str:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    if process.returncode != 0:
        raise RuntimeError(stderr.decode("utf-8", errors="replace"))
    return stdout.decode("utf-8", errors="replace")


async def create_worktree(source_repo: Path, worktree_path: Path, branch: str, base_branch: str) -> None:
    await run_command(["git", "fetch", "origin"], cwd=source_repo, timeout=120)
    await run_command(
        ["git", "worktree", "add", "-b", branch, str(worktree_path), f"origin/{base_branch}"],
        cwd=source_repo,
        timeout=120,
    )
```

### git diff 수집 함수 예시

```python
async def collect_diff(worktree_path: Path) -> dict[str, str]:
    status = await run_command(["git", "status", "--short"], cwd=worktree_path)
    diff_stat = await run_command(["git", "diff", "--stat"], cwd=worktree_path)
    diff = await run_command(["git", "diff", "--", "."], cwd=worktree_path)
    changed_files = await run_command(["git", "diff", "--name-only"], cwd=worktree_path)
    return {
        "status": status,
        "diff_stat": diff_stat,
        "diff": diff,
        "changed_files": changed_files,
    }
```

### Notion report 생성 의사코드

```python
async def create_notion_report(job: AgentJob, result: CodexRunResult, diff_summary: str | None) -> str:
    clean_stdout = redact_secrets(result.stdout)
    page = notion.pages.create(
        parent={"database_id": settings.NOTION_DATABASE_ID},
        properties={
            "Title": {"title": [{"text": {"content": f"팡이 episode: {job.job_type}"}}]},
            "Status": {"select": {"name": job.status}},
            "Requester": {"rich_text": [{"text": {"content": job.requester_slack_user_id}}]},
        },
        children=[
            paragraph("요약", clean_stdout[:1800]),
            paragraph("Diff", diff_summary or "변경 없음"),
        ],
    )
    return page["id"]
```

### GitHub PR 생성 의사코드

```python
async def create_pull_request(worktree_path: Path, branch: str, title: str, body: str) -> str:
    await run_command(["git", "add", "-A"], cwd=worktree_path)
    await run_command(["git", "commit", "-m", title], cwd=worktree_path)
    await run_command(["git", "push", "origin", branch], cwd=worktree_path, timeout=180)
    output = await run_command(
        ["gh", "pr", "create", "--title", title, "--body", body, "--base", "develop", "--head", branch],
        cwd=worktree_path,
        timeout=180,
    )
    return output.strip()
```

### Slack 승인 버튼 처리 의사코드

```python
@router.post("/slack/interactions")
async def slack_interactions(request: Request):
    body = await request.body()
    verify_slack_signature(request.headers, body)
    payload = parse_interaction_payload(body)

    action = payload["actions"][0]
    action_id = action["action_id"]
    job_id = action["value"]
    user_id = payload["user"]["id"]

    if not await approvals.can_approve(job_id, user_id):
        return {"text": "이 작업을 승인할 권한이 없습니다."}

    if action_id == "approve_edit":
        await approvals.approve(job_id, "workspace_write", user_id)
        job_queue.enqueue_edit(job_id)
        return {"text": "수정 작업을 시작합니다."}

    if action_id == "approve_pr":
        await approvals.approve(job_id, "create_pr", user_id)
        job_queue.enqueue_pr(job_id)
        return {"text": "PR 생성을 시작합니다."}

    if action_id == "cancel_job":
        await jobs.cancel(job_id, user_id)
        return {"text": "작업을 취소했습니다."}
```

## 11. Codex CLI 인증 기반 유지

현재 팡이 MVP는 API key 기반 직접 호출이 아니라 서버 계정의 Codex CLI 인증으로 `codex exec`를 실행한다.

아래 상황이 와도 먼저 Codex CLI 운영 안정화와 서버 계층 분리를 우선 검토한다.

- 여러 worker에서 안정적인 병렬 실행이 필요할 때
- Codex CLI session/auth 관리가 운영 부담이 될 때
- 실행 결과를 더 구조화된 형태로 받고 싶을 때
- 비용/사용량/권한을 팀 단위로 통제해야 할 때
- 서버 환경에서 interactive login 유지가 어려울 때

서버가 작업 상태, 승인, git, Slack/Notion/GitHub 기록을 통제하고, Codex CLI는 격리된 실행 도구로만 사용하는 구조를 유지한다.
