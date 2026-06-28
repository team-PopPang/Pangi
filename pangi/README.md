# Pangi Server

팡이 MVP 본체를 구현할 Python/FastAPI 패키지입니다.

`poppangbot/`은 Slack 연결 확인용 샘플로 유지하고, 실제 MVP 기능은 이 `pangi/` 패키지에 단계적으로 구현합니다.

## 현재 구현된 것

현재는 read-only 분석 흐름의 기본 경로까지 포함합니다.

- FastAPI 앱 객체
- `/health` 상태 확인 route
- `domain/usecase/repository/infra` 기준 패키지 구조
- 환경변수 기반 설정 로더
- Slack user/channel allowlist 파서
- source repo root 자동 탐색과 worktree root 설정
- Slack Events API route
- Slack slash command route
- Slack interactions placeholder
- Slack request signature 검증
- app mention 정규화와 retry 중복 방지
- 입력 가드레일 기반 외부 웹/쓰기 요청 조기 차단
- 입력 가드레일 1차 라우팅과 Codex CLI 기반 orchestrator 보조 분류
- Notion 문서 읽기 요청 분류와 공식 MCP 기반 Notion context provider
- Git MCP 기반 GitHub/Git context 요청 분류, repo catalog 응답, 조직 repo clone-on-demand
- `SlackThread`, `AgentJob`, `CodexRun`, `ScheduledTask`, `ScheduledTaskRun` 모델
- SQLite 기반 job 저장소
- Slack 요청을 `queued` 상태 job으로 저장
- Slack event id 기반 중복 job 방지
- job 상태 변경과 Codex run 기록 저장
- in-process background worker
- in-process scheduler
- FastAPI startup/shutdown 기반 worker lifecycle
- job 상태 전환: `queued` -> `running` -> `succeeded` / `failed` / `timed_out`
- worker progress hook
- 전체/repo별 동시 실행 제한
- job cancellation 기본 구조
- read-only 분석용 git worktree 생성
- 기본 base branch 설정
- `codex exec --sandbox read-only` 실행
- Codex stdout/stderr/exit code/timeout 저장
- Slack thread에 read-only 분석 성공/실패/timeout 결과 응답
- Slack/외부 출력 전 secret redaction과 길이 제한
- 관리자 홈 페이지
- 관리자 MCP 상태 페이지
- 관리자 스케줄 페이지와 예약 실행 기록
- deterministic Eval runner와 core/red-team case
- Slack Web API `chat.postMessage` 기반 접수/상태 메시지
- Slack Web API `reactions.add` 기반 요청 접수 `eyes` reaction
- 일반 대화와 read-only 분석 성공 응답 시 원본 메시지 `eyes` reaction을 `white_check_mark`로 전환
- 관리자 로그인 페이지
- 관리자용 SQLite DB 확인 페이지 `/pangi-admin/db`
- 관리자용 Notion OAuth 연결 페이지 `/pangi-admin/notion`
- health와 설정 테스트

Slack 요청을 받으면 검증과 정규화를 수행한 뒤 SQLite에 job으로 저장하고 background worker에 넘깁니다. worker는 허용된 source repo에서 read-only 분석용 detached worktree를 만들고, 그 worktree에서 Codex read-only 분석을 실행한 뒤 결과를 Slack thread에 반환합니다.

## 소스 구조

`pangi/src/pangi/`는 아래 책임으로 나눕니다.

```text
app.py                 FastAPI 앱과 의존성 조립
config/                환경변수와 설정
domain/                AgentJob, SlackThread, CodexRun 같은 핵심 모델과 정책
usecase/               Slack 요청 접수, job 실행, 입력 가드레일, request decision, prompt 생성 흐름
repository/            저장소 Protocol과 SQLite 구현
infra/                 FastAPI route, Slack API, queue, git, codex 같은 외부 기술 adapter
```

현재 주요 구현 위치:

```text
infra/slack/routes.py                  Slack Events API route
infra/slack/client.py                  Slack Web API client
infra/orchestrator/codex_orchestrator.py  Codex CLI 기반 보조 요청 분류 adapter
infra/notion/__init__.py               Notion context provider registry
infra/git_mcp/__init__.py              Git MCP context provider registry
infra/queue/in_process_queue.py        in-process background worker
infra/git/worktree_manager.py          read-only 분석용 git worktree 생성
infra/codex/runner.py                  Codex read-only 실행 adapter
usecase/input_guardrail.py             외부 웹/쓰기 요청 차단과 orchestrator decision 보정
usecase/notion_context.py              Notion context prompt 주입 helper
usecase/git_context.py                 Git context prompt 주입과 repo catalog helper
usecase/request_decision.py            요청 분기 decision 타입
prompts/pangi_agent.md                 PopPang 코드/개발/커밋/디자인 공통 스타일 프롬프트
prompts/orchestrator.md                Slack 요청 분류용 orchestrator 프롬프트
prompts/git_context.md                 Git MCP context 답변 모드 프롬프트
prompts/chat.md                        일반 대화 모드 프롬프트
prompts/read_only_analysis.md          read-only 코드 분석 모드 프롬프트
repository/job_repository_protocol.py     저장소 Protocol
repository/job_repository_sqlite_impl.py  SQLite job 저장소 구현체
domain/models.py                       SlackThread, AgentJob, CodexRun 모델
```

## 아직 구현되지 않은 것

- 실제 Slack 환경에서의 1차 MVP end-to-end 검증
- worktree cleanup 정책
- PR 승인 전 diff 수집/검토 흐름
- Notion DB context 선별 품질 고도화
- Notion 기록
- 코드 수정 승인 흐름
- PR 생성 흐름

## 가상환경 만들기

로컬 개발은 `pangi/.venv` 가상환경에서 실행합니다. Python 가상환경은 경로를 내부에 기록하므로, 기존 루트 `.venv`를 옮기기보다 아래처럼 `pangi/.venv`를 새로 만드는 쪽이 안전합니다.

```bash
cd pangi
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
```

다음에 다시 작업할 때는 가상환경만 활성화하면 됩니다.

```bash
cd pangi
source .venv/bin/activate
```

프롬프트 앞에 `(.venv)`가 보이면 `pangi/.venv`가 활성화된 상태입니다.

## requirements

로컬 실행과 테스트에 필요한 패키지는 `requirements.txt`에 정리합니다.

```text
fastapi
uvicorn[standard]
pydantic-settings
pytest
```

## 환경변수

실제 값은 `.env` 같은 로컬 파일이나 배포 환경에만 저장하고 커밋하지 않습니다. 예시는 `.env.example`을 참고합니다.

로컬에서는 먼저 예시 파일을 복사합니다.

```bash
cd pangi
cp .env.example .env
```

그 다음 `.env`의 값을 로컬 환경에 맞게 채웁니다. `/health`만 확인할 때는 `.env.example`에 들어 있는 Slack 관련 dummy 값을 그대로 써도 됩니다. `.env`에 빈 값이 있으면 앱은 `.env.example`의 dummy 값을 fallback으로 사용합니다.

이미 `.env`가 있는데 Slack 값이 비어 있으면 서버가 시작되지 않습니다. 이 경우 `.env`에서 아래 네 값을 채웁니다.

```env
SLACK_SIGNING_SECRET=dummy-local-signing-secret
SLACK_BOT_TOKEN=dummy-local-bot-token
SLACK_ALLOWED_USER_IDS=U_LOCAL
SLACK_ALLOWED_CHANNEL_IDS=C_LOCAL
```

임시 개발 환경에서 모든 Slack user 또는 channel을 허용하려면 allowlist 값에 `*`를 넣을 수 있습니다.

```env
SLACK_ALLOWED_USER_IDS=*
SLACK_ALLOWED_CHANNEL_IDS=*
```

처음 설정이라면 아래 스크립트로 `.env`를 만들 수 있습니다. 기존 `.env`가 있으면 덮어쓰지 않습니다.

```bash
cd pangi
./scripts/init-local-env.sh
```

필수 값:

- `SLACK_SIGNING_SECRET`
- `SLACK_BOT_TOKEN`
- `SLACK_ALLOWED_USER_IDS`
- `SLACK_ALLOWED_CHANNEL_IDS`
- `PANGI_WORKTREE_ROOT`
- `PANGI_SOURCE_REPO_ROOT`

선택 값:

- `PANGI_DEFAULT_BASE_BRANCH`: read-only 분석에서 먼저 시도할 기준 branch입니다. 기본값은 `develop`입니다. 이 branch가 없으면 `main`을 한 번 더 시도합니다.
- `PANGI_JOB_TIMEOUT_SECONDS`: 기본값은 600초입니다.
- `PANGI_CHAT_MODEL`: repo를 읽지 않는 일반 대화 모델입니다. 기본값은 `gpt-5.4-mini`입니다.
- `PANGI_CHAT_REASONING_EFFORT`: 일반 대화용 Codex 호출의 추론 난이도입니다. 기본값은 `low`입니다.
- `PANGI_ORCHESTRATOR_MODEL`: 입력 가드레일을 통과한 요청을 라우팅하는 모델입니다. 기본값은 `gpt-5.4-mini`입니다.
- `PANGI_ORCHESTRATOR_REASONING_EFFORT`: orchestrator용 Codex 호출의 추론 난이도입니다. 기본값은 `low`입니다.
- `PANGI_ORCHESTRATOR_TIMEOUT_SECONDS`: orchestrator Codex 호출 timeout입니다. 기본값은 20초입니다.
- `PANGI_ANALYSIS_MODEL`: read-only worktree에서 repo 코드를 읽는 분석 모델입니다. 기본값은 `gpt-5.5`입니다.
- `PANGI_ANALYSIS_REASONING_EFFORT`: repo 분석용 Codex 호출의 추론 난이도입니다. 기본값은 `high`입니다.
- `PANGI_PUBLIC_BASE_URL`: Notion OAuth callback을 받을 공개 서버 URL입니다. 비우면 요청 host 기준으로 callback URL을 만듭니다.
- `PANGI_NOTION_ENABLED`: Notion context provider 사용 여부입니다. 기본값은 `0`입니다.
- `PANGI_NOTION_MCP_URL`: 공식 Notion MCP endpoint입니다. 기본값은 `https://mcp.notion.com/mcp`입니다.
- `PANGI_NOTION_ALLOWED_PAGE_IDS`: 팡이가 읽을 수 있는 Notion page id allowlist입니다.
- `PANGI_NOTION_ALLOWED_DATABASE_IDS`: 팡이가 읽을 수 있는 Notion database id allowlist입니다.
- `PANGI_NOTION_CONTEXT_MAX_CHARS`: Codex prompt에 붙일 Notion context 최대 길이입니다. 기본값은 6000입니다.
- `PANGI_NOTION_TIMEOUT_SECONDS`: Notion context 조회 timeout입니다. 기본값은 20초입니다.
- `PANGI_NOTION_TOKEN_STORE_PATH`: Notion OAuth token store 경로입니다. 비우면 worktree root 아래 `_notion/notion-oauth.json`을 사용합니다.
- `PANGI_NOTION_WRITE_ENABLED`: 예약 설정값입니다. MVP에서는 Notion write 요청을 지원하지 않습니다.
- `PANGI_SCHEDULER_ENABLED`: 관리자 페이지에 저장된 예약 작업의 자동 실행 여부입니다. 기본값은 `0`입니다.
- `PANGI_SCHEDULER_TICK_SECONDS`: scheduler가 due schedule을 확인하는 간격입니다. 기본값은 30초입니다.
- `PANGI_ENABLE_ADMIN_PAGES`: 관리자 페이지를 열려면 `1`로 설정합니다. 기본값은 `0`입니다.
- `PANGI_ADMIN_PASSWORD`: 관리자 페이지 비밀번호입니다. 관리자 페이지를 켤 때만 필요합니다.

`PANGI_SOURCE_REPO_ROOT` 아래 direct child 디렉터리 이름을 그대로 repo key로 사용합니다.
예를 들어 `.../repos/PopPang-iOS`가 있으면 Slack에서는 `PopPang-iOS`로 요청할 수 있습니다.

read-only 분석용 worktree는 `PANGI_WORKTREE_ROOT/{job_id}` 아래에 만들어집니다. 현재 MVP에서는 새 작업 branch를 만들지 않고 `origin/{base_branch}`를 detached checkout으로 가져와 Codex가 읽을 격리 폴더로 사용합니다.

```env
PANGI_DEFAULT_BASE_BRANCH=develop
```

동작은 단순합니다. 먼저 `origin/develop`을 시도하고, 해당 branch가 없으면 `origin/main`을 시도합니다.

Slack 앱의 Bot Token Scopes에는 최소 `app_mentions:read`, `chat:write`, `reactions:write`가 필요합니다.

## 로컬 실행

서버 시작 시 필수 환경변수를 검증합니다. `pangi/.env`가 있으면 자동으로 읽습니다.

```bash
cd pangi
source .venv/bin/activate
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

## 관리자 페이지

SQLite job 기록을 브라우저에서 확인하려면 `.env`에 아래 값을 설정하고 서버를 재시작합니다.

```env
PANGI_ENABLE_ADMIN_PAGES=1
PANGI_ADMIN_PASSWORD=change-this-password
```

접속 주소:

```text
http://127.0.0.1:8000/pangi-admin
```

로그인 정보:

- 아이디: `pangi`
- 비밀번호: `PANGI_ADMIN_PASSWORD`에 설정한 값

로그인 후 `/pangi-admin` 홈에서 DB 기록, 스케줄, MCP 상태, Notion 연결 화면으로 이동할 수 있습니다.
`/pangi-admin/db`에서 최근 `agent_jobs`, `slack_threads`, `codex_runs`를 확인할 수 있습니다.
`/pangi-admin/schedules`에서는 `once`, `daily`, `weekly` 예약 작업을 등록하고 실행 기록을 확인할 수 있습니다. 실제 자동 실행은 `PANGI_SCHEDULER_ENABLED=1`일 때만 동작합니다.
`/pangi-admin/mcp`에서는 Notion/Git MCP 설정과 endpoint 목록을 확인할 수 있으며 token 값은 표시하지 않습니다.

Notion context를 사용하려면 로그인 후 `/pangi-admin/notion`에서 Notion OAuth 연결을 완료합니다. 운영 서버에서는 `PANGI_PUBLIC_BASE_URL`을 외부에서 접근 가능한 팡이 서버 URL로 설정합니다.

## 테스트

```bash
cd pangi
source .venv/bin/activate
pytest
```

프롬프트, 모델, provider, toolset 변경 후에는 Eval suite도 실행합니다.

```bash
PYTHONPATH=src python3 -m pangi.evaluations.run
```

Eval은 답변 정답을 채점하기보다 분류, provider 호출, 금지 호출, Codex read-only 경계, secret redaction 같은 행동 계약을 검증합니다.

## 배포 스크립트

- `deploy-bot.sh`: 원격 앱 디렉터리를 지우고 다시 올리는 full deploy 용도입니다. 초기화나 비상 복구에 가깝습니다.
- `deploy.sh`: 운영용 code-only deploy입니다. 원격 `.env`, `.data`, `nohup.out`을 보존한 채 `src/`, `pyproject.toml`, `requirements.txt`, `README.md`만 동기화합니다.

운영용 배포 예:

```bash
cd pangi
./deploy.sh
```

테스트까지 같이 올리고 싶으면:

```bash
cd pangi
SYNC_TESTS=1 ./deploy.sh
```
