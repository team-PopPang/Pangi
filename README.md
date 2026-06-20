# Pangi

팡이는 PopPang 팀 전용 Slack 기반 개발 에이전트입니다.

Slack에서 `@팡이`를 부르면 팡이는 기본적으로 AI 대화로 답합니다.
허용된 PopPang repo를 명확히 분석해달라고 요청하면, 안전한 read-only worktree를 만든 뒤 Codex CLI로 코드를 읽고 결과를 Slack thread에 답합니다.

```text
팡이가 먼저 읽고, 팀이 더 빠르게 판단합니다.
```

## 지금 팡이가 할 수 있는 것

- Slack에서 `@팡이` mention과 slash command 요청을 받을 수 있습니다.
- 허용된 user/channel/repo allowlist를 통과한 요청만 처리합니다.
- 일반 대화, 문장 정리, repo를 직접 읽지 않는 간단한 판단은 Codex chat으로 답합니다.
- 허용된 PopPang repo 이름이 명시된 분석 요청은 SQLite job으로 저장하고, 격리된 read-only worktree에서 `codex exec --sandbox read-only`로 코드를 읽은 뒤 Slack thread에 결과를 남깁니다.
- 외부 웹/URL 분석, 코드 수정, PR 생성, 배포, commit/push 요청은 입력 가드레일에서 차단하고 안내 응답만 보냅니다.
- 요청을 받으면 원본 Slack 메시지에 `eyes` reaction을 달고, 일반 대화나 read-only 분석 응답에 성공하면 `white_check_mark`로 전환합니다. 실패나 timeout은 `x`로 전환합니다.
- 관리자 DB 확인 페이지에서 Slack thread, job, Codex run 기록을 확인할 수 있습니다.

## 팡이의 생명력을 바꾸는 곳

팡이의 말투, 판단 감각, PopPang다운 개발/디자인 스타일은 prompt 파일에서 조정합니다.

- `pangi/src/pangi/prompts/pangi_agent.md`: 팡이의 공통 성격, 답변 톤, 코드/개발/커밋/디자인 감각
- `pangi/src/pangi/prompts/chat.md`: repo를 읽지 않는 일반 대화 모드
- `pangi/src/pangi/prompts/read_only_analysis.md`: repo를 read-only로 읽고 분석하는 모드
- `pangi/src/pangi/prompts/orchestrator.md`: 입력 가드레일을 통과한 요청을 일반 대화, repo 확인 질문, repo 분석 job으로 나누는 요청 분류 규칙

팡이의 "생명력"을 더 넣고 싶다면 먼저 `pangi_agent.md`를 수정합니다. 요청 분류 기준을 바꾸고 싶을 때만 `orchestrator.md`를 수정합니다.

## 목표

1차 MVP의 목표는 아래 흐름을 안정적으로 완성하는 것입니다.

```mermaid
flowchart TD
    A["Slack에서 @팡이 호출"] --> B["FastAPI Slack webhook 수신"]
    B --> C["Slack signature와 allowlist 검증"]
    C --> D["입력 가드레일<br/>외부 웹/쓰기 요청 조기 차단"]
    D -->|차단| X["안내 응답 후 종료"]
    D -->|통과한 요청만 전달| E["gpt-5.5 Orchestrator<br/>요청 분류와 라우팅"]
    E -->|일반 대화| Y["Codex chat 응답"]
    E -->|repo 불명확| Z["repo 확인 질문 후 종료"]
    E -->|허용 repo 분석| F["SQLite에 AgentJob 저장"]
    F --> G["Background worker 실행"]
    G --> H["허용된 source repo 확인"]
    H --> I["Read-only git worktree 생성"]
    I --> J["Codex exec --sandbox read-only 실행"]
    J --> K["stdout, stderr, exit code, timeout 저장"]
    K --> L["Slack thread에 결과 응답"]
```

현재 단계에서 팡이는 코드를 수정하지 않습니다. 일반 대화는 repo job 없이 답하고, repo 분석 요청은 코드를 읽고 확인한 사실과 근거를 정리하는 역할에 집중합니다.
외부 웹/인터넷 URL 분석은 서버 부하와 보안 이유로 지원하지 않습니다.

## 현재 구현된 것

- FastAPI 앱과 `/health` 상태 확인
- Slack Events API와 slash command 수신
- Slack request signature 검증
- Slack user/channel allowlist
- repo allowlist와 worktree root 설정
- Slack app mention 정규화와 retry 중복 방지
- 입력 가드레일 기반 외부 웹/쓰기 요청 조기 차단
- gpt-5.5 orchestrator adapter
- Codex chat 응답 경로
- 외부 웹/인터넷 분석 요청 차단
- SQLite 기반 `SlackThread`, `AgentJob`, `CodexRun` 저장소
- in-process background worker
- job 상태 전환: `queued`, `running`, `succeeded`, `failed`, `timed_out`, `cancelled`
- repo별/전체 동시 실행 제한
- read-only 분석용 git worktree 생성
- `develop` branch 우선, 없으면 `main` branch fallback
- `codex exec --sandbox read-only` 실행
- Codex stdout/stderr/exit code/timeout 저장
- Slack thread에 성공/실패/timeout 결과 응답
- Slack 원본 메시지에 `eyes` reaction 추가, 일반 대화와 read-only 분석 응답 성공 시 `white_check_mark`로 전환
- Slack/외부 출력 전 secret redaction과 길이 제한
- 관리자 DB 확인 페이지 `/pangi-admin/db`

## 아직 남은 것

- 실제 Slack 환경 end-to-end 검증
- worktree cleanup 정책
- PR 승인 전 diff 수집/검토 흐름
- Notion 기록
- 코드 수정 승인 흐름
- PR 생성 흐름

## AgentJob과 thread 관리

팡이는 Slack thread를 독립된 대화 단위로 보고, repo 분석 요청 하나를 `AgentJob` 하나로 저장합니다.

```mermaid
flowchart TD
    A["Slack app mention 또는 slash command"] --> B["SlackCommand 정규화"]
    B --> C["thread key 계산<br/>team_id + channel_id + thread_ts"]
    C --> D["slack_threads 조회 또는 생성"]
    D --> E["agent_jobs 생성"]
    E --> F["slack_threads.last_job_id 갱신"]
    E --> G["background worker에 job_id enqueue"]
    G --> H["CodexRun 기록 저장"]
    H --> I["같은 Slack thread에 결과 응답"]
```

### thread를 나누는 기준

`slack_threads`는 `team_id`, `channel_id`, `thread_ts` 조합을 unique key로 사용합니다.

| 기준 | 의미 |
| --- | --- |
| `team_id` | Slack workspace 단위 |
| `channel_id` | Slack channel 단위 |
| `thread_ts` | Slack thread 단위 |

같은 channel 안에서도 `thread_ts`가 다르면 서로 다른 대화로 저장됩니다. 그래서 A thread에서 진행한 분석 job과 B thread에서 진행한 분석 job은 같은 repo를 보더라도 별도의 `slack_threads` row와 `agent_jobs` row로 관리됩니다.

### app mention의 thread 계산

app mention 이벤트는 아래 규칙으로 `thread_ts`를 정합니다.

| 상황 | 사용하는 값 |
| --- | --- |
| 기존 Slack thread 안에서 팡이를 부른 경우 | event의 `thread_ts` |
| 새 메시지에서 팡이를 부른 경우 | 원본 event의 `ts` |

이렇게 하면 새 메시지에서 시작된 요청도 그 메시지 자체를 thread root로 삼아 이후 답변이 같은 Slack thread에 달립니다.

### AgentJob이 저장하는 것

`AgentJob`은 Slack 요청을 실행 가능한 background job으로 바꾼 기록입니다.

| 정보 | 설명 |
| --- | --- |
| Slack 위치 | `slack_thread_id`, `slack_team_id`, `slack_channel_id`, `slack_thread_ts` |
| 원본 메시지 | `slack_message_ts`, `requester_user_id`, `prompt` |
| 실행 대상 | `repo_key`, `job_type` |
| 실행 상태 | `status`, `worktree_path`, `stdout`, `stderr`, `error_message` |

`slack_thread_id`로 `slack_threads`에 연결되기 때문에 job 결과는 항상 요청이 들어온 thread로 돌아갑니다. `event_id`는 unique하게 저장해서 Slack retry가 와도 같은 요청으로 job이 중복 생성되지 않게 막습니다.

`slack_message_ts`는 thread 구분용이 아니라 원본 메시지 reaction 교체용입니다. app mention 요청에서는 원본 메시지의 `eyes` reaction을 완료 후 `white_check_mark` 또는 `x`로 바꿀 때 사용하고, slash command처럼 원본 메시지 reaction을 관리하지 않는 요청에서는 비어 있을 수 있습니다.

## SQLite 테이블 구조

현재 SQLite 구현 기준은 `pangi/src/pangi/repository/job_repository_sqlite_impl.py`입니다.

### `slack_threads`

Slack thread 단위 대화 컨텍스트를 저장합니다.

| 컬럼 | 타입 | 제약 | 설명 |
| --- | --- | --- | --- |
| `id` | `TEXT` | `PRIMARY KEY` | 내부 Slack thread id |
| `team_id` | `TEXT` | `NOT NULL` | Slack team id |
| `channel_id` | `TEXT` | `NOT NULL` | Slack channel id |
| `thread_ts` | `TEXT` | `NOT NULL` | Slack thread timestamp |
| `last_job_id` | `TEXT` |  | 마지막으로 연결된 job id |
| `created_at` | `TEXT` | `NOT NULL` | 생성 시각 |
| `updated_at` | `TEXT` | `NOT NULL` | 수정 시각 |

추가 제약:

| 제약 | 컬럼 |
| --- | --- |
| `UNIQUE` | `team_id`, `channel_id`, `thread_ts` |

### `agent_jobs`

Slack 요청 하나를 팡이 job 하나로 저장합니다.

| 컬럼 | 타입 | 제약 | 설명 |
| --- | --- | --- | --- |
| `id` | `TEXT` | `PRIMARY KEY` | 내부 job id |
| `event_id` | `TEXT` | `NOT NULL`, `UNIQUE` | Slack event id 또는 slash command trigger id |
| `slack_thread_id` | `TEXT` | `NOT NULL`, `FOREIGN KEY` | `slack_threads.id` 참조 |
| `slack_team_id` | `TEXT` | `NOT NULL` | Slack team id |
| `slack_channel_id` | `TEXT` | `NOT NULL` | Slack channel id |
| `slack_thread_ts` | `TEXT` | `NOT NULL` | Slack thread timestamp |
| `slack_message_ts` | `TEXT` |  | 원본 app mention message timestamp |
| `requester_user_id` | `TEXT` | `NOT NULL` | 요청한 Slack user id |
| `job_type` | `TEXT` | `NOT NULL` | job 종류 |
| `status` | `TEXT` | `NOT NULL` | job 상태 |
| `repo_key` | `TEXT` | `NOT NULL` | allowlist에 등록된 repo key |
| `prompt` | `TEXT` | `NOT NULL` | 사용자 요청 원문 |
| `worktree_path` | `TEXT` |  | job별 read-only worktree 경로 |
| `stdout` | `TEXT` |  | Codex 실행 stdout |
| `stderr` | `TEXT` |  | Codex 실행 stderr |
| `error_message` | `TEXT` |  | 실패 요약 메시지 |
| `created_at` | `TEXT` | `NOT NULL` | 생성 시각 |
| `updated_at` | `TEXT` | `NOT NULL` | 수정 시각 |

`slack_message_ts`는 원본 메시지의 `eyes` reaction을 `white_check_mark` 또는 `x`로 바꿀 때 사용합니다. slash command나 legacy job에서는 비어 있을 수 있습니다.

현재 `status` 값:

| 값 |
| --- |
| `queued` |
| `running` |
| `succeeded` |
| `failed` |
| `timed_out` |
| `cancelled` |
| `waiting_approval` |
| `rejected` |

현재 `job_type` 값:

| 값 |
| --- |
| `analyze` |
| `edit_requested` |
| `pr_summary` |
| `troubleshooting` |
| `xcodebuild_failure` |

### `codex_runs`

job 안에서 실행된 Codex 실행 기록을 저장합니다.

| 컬럼 | 타입 | 제약 | 설명 |
| --- | --- | --- | --- |
| `id` | `TEXT` | `PRIMARY KEY` | 내부 Codex run id |
| `job_id` | `TEXT` | `NOT NULL`, `FOREIGN KEY` | `agent_jobs.id` 참조 |
| `mode` | `TEXT` | `NOT NULL` | Codex 실행 모드 |
| `command` | `TEXT` | `NOT NULL` | 실행한 argv list의 JSON 문자열 |
| `prompt` | `TEXT` | `NOT NULL` | Codex에 전달한 prompt |
| `stdout` | `TEXT` |  | Codex stdout |
| `stderr` | `TEXT` |  | Codex stderr |
| `exit_code` | `INTEGER` |  | 프로세스 종료 코드 |
| `timed_out` | `INTEGER` | `NOT NULL` | timeout 여부, `0` 또는 `1` |
| `started_at` | `TEXT` | `NOT NULL` | 시작 시각 |
| `finished_at` | `TEXT` |  | 종료 시각 |

## 로컬 실행

```bash
cd pangi
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
cp .env.example .env
```

`.env`를 로컬 환경에 맞게 채운 뒤 실행합니다.

```bash
uvicorn pangi.app:app --reload --port 8000
```

상태 확인:

```bash
curl http://127.0.0.1:8000/health
```

정상 응답:

```json
{"status":"ok"}
```

## 환경변수

실제 secret은 `.env` 또는 배포 환경에만 저장합니다. `.env`는 git에 올리지 않습니다.

필수 값:

```env
SLACK_SIGNING_SECRET=
SLACK_BOT_TOKEN=
SLACK_ALLOWED_USER_IDS=
SLACK_ALLOWED_CHANNEL_IDS=
PANGI_ALLOWED_REPOS=
PANGI_WORKTREE_ROOT=
PANGI_SOURCE_REPO_ROOT=
```

선택 값:

```env
PANGI_DEFAULT_BASE_BRANCH=develop
PANGI_JOB_TIMEOUT_SECONDS=600
PANGI_CHAT_TIMEOUT_SECONDS=120
PANGI_CHAT_WORKSPACE_ROOT=
OPENAI_API_KEY=
PANGI_ORCHESTRATOR_MODEL=gpt-5.5
PANGI_ORCHESTRATOR_REASONING_EFFORT=medium
PANGI_ORCHESTRATOR_SERVICE_TIER=default
PANGI_ENABLE_ADMIN_PAGES=0
PANGI_ADMIN_PASSWORD=
```

`OPENAI_API_KEY`가 있으면 입력 가드레일을 통과한 요청을 gpt-5.5 orchestrator로 분류합니다. 없으면 로컬 개발과 테스트를 위해 deterministic orchestrator로 fallback합니다.

임시 개발 환경에서 모든 Slack user/channel을 허용하려면 `*`를 사용할 수 있습니다.

```env
SLACK_ALLOWED_USER_IDS=*
SLACK_ALLOWED_CHANNEL_IDS=*
```

repo allowlist 예시:

```env
PANGI_SOURCE_REPO_ROOT=/home/poppang/repos
PANGI_ALLOWED_REPOS=PopPang-iOS=/home/poppang/repos/PopPang-iOS
PANGI_WORKTREE_ROOT=/home/poppang/worktrees
```

branch 선택은 단순합니다.

```text
1. origin/develop 시도
2. develop이 없으면 origin/main 시도
3. 둘 다 없으면 job 실패
```

## Slack 앱 권한

Bot Token Scopes 최소 권한:

```text
app_mentions:read
chat:write
reactions:write
```

scope를 바꾼 뒤에는 Slack 앱을 workspace에 다시 설치해야 변경이 적용됩니다.

## Codex CLI

팡이 서버를 실행하는 계정에 Codex CLI가 설치되어 있어야 합니다.

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | CODEX_NON_INTERACTIVE=1 sh
export PATH="$HOME/.local/bin:$PATH"
codex --version
codex login --device-auth
```

서버에서 확인:

```bash
cd /path/to/git/repo
codex exec --sandbox read-only "이 저장소 구조를 한 문단으로 요약해줘"
```

Codex는 git repo 안에서 실행되어야 합니다. 팡이는 job마다 git worktree를 만들고 그 안에서 Codex를 실행합니다.
일반 대화 응답은 repo worktree가 아니라 `PANGI_CHAT_WORKSPACE_ROOT`에서 `codex exec --skip-git-repo-check --sandbox read-only`로 실행합니다.

## 관리자 페이지

관리자 DB 확인 페이지는 기본 비활성화입니다.

```env
PANGI_ENABLE_ADMIN_PAGES=1
PANGI_ADMIN_PASSWORD=change-this-password
```

실행 후 접속:

```text
http://127.0.0.1:8000/pangi-admin/login
```

## 테스트

```bash
cd pangi
source .venv/bin/activate
pytest
```

## 안전 원칙

- `.env`, token, signing secret, Codex auth 파일을 git에 올리지 않습니다.
- Slack user/channel/repo allowlist를 강제합니다.
- 사용자의 Slack 메시지를 shell command로 직접 실행하지 않습니다.
- 외부 명령은 argv list로 실행하고 `shell=True`를 사용하지 않습니다.
- Codex 분석은 `--sandbox read-only`로 실행합니다.
- Codex는 원본 source repo가 아니라 job별 worktree에서 실행합니다.
- 코드 수정과 PR 생성은 Slack 승인 흐름이 붙은 뒤에만 구현합니다.

## 문서

- [MVP 개요](docs/mvp/overview.md)
- [구현 체크리스트](docs/implementation-checklist.md)
- [안전 규칙](docs/security/safety-rules.md)
- [아키텍처 문서](docs/architecture/)

## 구조

```text
pangi/
  README.md
  pyproject.toml
  requirements.txt
  src/pangi/
    app.py
    config/
    domain/
    usecase/
    repository/
    infra/
      slack/
      queue/
      git/
      codex/
      admin/
      logger/
      approvals/
  tests/

docs/
  mvp/
  architecture/
  security/
  reference/
```

주요 책임은 아래처럼 나눕니다.

```text
config/       환경변수와 설정
domain/       핵심 모델과 정책
usecase/      Slack 요청 접수, 분석 job 실행, prompt 생성
repository/   저장소 Protocol과 SQLite 구현
infra/        Slack API, queue, git, Codex, admin route 같은 외부 adapter
```
