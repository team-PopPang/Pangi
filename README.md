# Pangi

조직이 직접 설치하고 운영하는 경량 Agent Runtime이다.

현재 개발 단계는 Pre-alpha다. WBS-01부터 WBS-04까지 완료했고, WBS-05는 Run Core, 생성·조회와 영속 Queue·복구 기반까지 구현했다. 자연어 요청을 모델과 Tool로 실행하는 완성된 Agent Runtime은 아직 제공하지 않는다.

## 현재 구현 상태

| WBS | 상태 | 지금까지 구현한 범위 |
| --- | --- | --- |
| 01. Foundation과 Package 경계 | 완료 | Python 3.11+ wheel, 계층별 Package 경계, Public API, Plugin·Capability Pack 등록 기반 |
| 02. 설정·Runtime Data·CLI | 완료 | 설정과 경로 해석, `init`, `start`, `status`, `doctor`, `config`, `migrate`, Bootstrap 복구 명령 |
| 03. SQLite 영속성과 Migration | 완료 | 단일 Process Lock, 직렬화된 Unit of Work, Migration·Checksum·Backup, Snapshot 검증 |
| 04. Web/API Shell과 인증 | 완료 | FastAPI Runtime, React Admin Shell, Bootstrap Admin, Local Login·Session·CSRF·역할 검사, OpenAPI Type 동기화 |
| 05. Run 상태·Queue·Event | 진행 중 | Run/Step/Event 계약과 Schema, 생성·조회·Idempotency, SQLite Queue Claim, Semaphore, Lease·Heartbeat, 취소와 재시작 복구 |
| 06~20 | 예정 | Guardrail, Model Routing, Orchestrator, MCP, Subagent, Skill, Scheduler, Slack, 관측성, 운영 배포 |

전체 작업 순서와 완료 조건은 [Pangi 1.0 구현 WBS](docs/chunks/README.md)에서 관리한다. 구현 결정과 전체 구조는 [Pangi 1.0 재설계 구현 설계서](docs/pangi-rebuild-implementation-design.md)에서 확인할 수 있다.

## 구현된 기능

### Package와 확장 경계

- Domain, Application, Adapter의 의존 방향을 Architecture Test로 검사한다.
- `PangiRuntime`과 필요한 Domain 계약만 Package Root의 Public API로 노출한다.
- Provider, Channel, Secret Store, Subagent를 Python Entry Point로 발견할 수 있다.
- Built-in Resource, 선택 설치 Capability Pack, UI 정적 Asset과 Runtime Data를 분리한다.

### CLI와 Runtime

- OS 기본 경로와 프로젝트 로컬 `.pangi/` 경로를 지원한다.
- `pangi init`이 설정과 Runtime Directory를 만들고 SQLite Migration을 적용한다.
- `pangi start`가 SQLite와 FastAPI/Uvicorn Runtime을 하나의 Process로 실행한다.
- `pangi status`가 열린 Port가 아니라 Pangi의 Live 응답을 확인한다.
- `pangi doctor`가 경로, 설정, SQLite, Process와 Package 상태를 읽기 전용으로 진단한다.
- `pangi migrate plan|apply`가 Migration 계획, Checksum과 사전 Backup을 관리한다.

### SQLite 영속성

- Process File Lock으로 같은 Runtime Data를 두 Process가 동시에 열지 못하게 한다.
- 하나의 Connection과 직렬화된 Unit of Work로 Commit과 Rollback 경계를 강제한다.
- Migration Registry가 순서와 Checksum을 검증하고 실패한 Migration 전체를 Rollback한다.
- DB Snapshot과 Manifest의 Hash, `quick_check`, 경로 안전성을 검증한다.

### Web Shell과 Local 인증

- `/health/live`와 `/health/ready`가 Runtime 상태를 구분해 반환한다.
- 일회성 Bootstrap URL로 최초 Local Admin을 생성한다. 원문 Token은 DB에 저장하지 않는다.
- Local Login, Session 확인·회전·Logout과 비활성 사용자 차단을 지원한다.
- Same-origin, CSRF, Secure Cookie와 역할 기반 API Dependency를 적용한다.
- React/Vite Admin Shell과 역할별 Navigation을 wheel 정적 Asset으로 제공한다.
- Backend OpenAPI와 Frontend Type의 변경 불일치를 검증한다.

### Run Core, 생성·조회와 영속 Queue

- `Principal`, `RunRequest`, `Run`, `RunStep`, `RunEvent`와 상태 전이 규칙을 제공한다.
- Run, 첫 `run.received` Event와 Idempotency 결과를 하나의 Transaction으로 저장한다.
- 같은 Principal·Route·Idempotency Key의 정확한 재시도는 기존 Run을 반환한다.
- 다른 요청이 같은 Idempotency Key를 사용하면 충돌로 거부한다. 기본 보존 시간은 24시간이다.
- `(created_at DESC, id DESC)` Keyset Cursor로 Run 목록을 조회한다.
- Member, Skill Author와 System은 자신이 소유한 Run만 조회한다. Admin은 전체 Run을 조회할 수 있다.
- 목록은 Metadata만 반환하고 상세 조회는 Owner 검사를 통과한 뒤 정규화된 요청을 복원한다.
- `queued_at`, `created_at`, `id` 순서로 가장 오래된 Run을 `BEGIN IMMEDIATE` Transaction에서 한 번만 Claim한다.
- Claim과 Queue 상태 변경은 Revision CAS를 사용하고 Heartbeat는 현재 Worker와 유효 Lease가 일치할 때만 갱신한다.
- Process-local Queue Runtime이 `asyncio.Event`로 깨어나고 `max_concurrent_runs` Semaphore로 동시 실행 수를 제한한다.
- 대기·실행 Run 취소와 오래된 Worker 쓰기 거부를 지원한다.
- 재시작 시 만료된 Run을 복구한다. 실행 중인 Step이 없거나 모두 Idempotent면 재대기하고 Non-idempotent Step이 있으면 안전하게 실패시킨다.
- Queue 상태와 `run.queued`, `run.running`, `run.interrupted`, `run.cancelled`, `run.failed` Event를 같은 Transaction에 저장한다.

Run 생성·조회와 Queue는 현재 Application Service, SQLite Store와 주입형 Queue Runtime까지 구현됐다. 실제 실행 Handler와 ASGI Composition Root에는 아직 연결하지 않았으며 HTTP API, Admin UI와 CLI에서도 호출할 수 없다.

## 아직 구현되지 않은 기능

- Root Orchestrator와 실제 실행 Handler의 Queue Runtime 연결, Lease·Heartbeat 운영 기본값
- Owner 기반 Run 취소 API, 일반 Event 조회, Visibility Filter, SSE, Queue Metric과 Run HTTP API
- 입력·Tool·출력 Guardrail과 Audit
- Model Provider Routing과 데이터 반출 정책
- Root Orchestrator, 실행 Engine, MCP, Subagent와 Web Search
- Skill, Workflow UI, Memory, Scheduler와 Eval
- Slack 요청 수신과 응답 전달
- Analytics, Feedback, API Key·IP Policy와 운영 Upgrade/Rollback

따라서 현재 Runtime은 설치·저장소·인증·Admin Shell, Run 영속성과 Queue 복구 기반을 검증하는 단계다. 실제 업무 요청을 처리하는 Agent 기능은 후속 WBS에서 연결한다.

## 개발 환경 준비

Python 3.11 이상과 `uv`가 필요하다. Backend 개발 의존성을 잠금 파일에 맞춰 설치한다.

```bash
uv sync --extra dev --python 3.11
```

Admin UI를 수정하거나 검증하려면 Node.js와 npm도 준비한다.

```bash
npm --prefix ui ci
```

## 로컬에서 실행하기

아래 예시는 Runtime Data를 저장소의 `.pangi/`에 만드는 프로젝트 로컬 모드다. `pangi init`이 이 경로를 `.gitignore`에 한 번만 추가한다.

```bash
uv run pangi init --project-local --yes
uv run pangi config validate --project-local
uv run pangi migrate plan --project-local
uv run pangi doctor --project-local --offline
uv run pangi start --project-local
```

`pangi init`이 출력한 `http://127.0.0.1:8787/bootstrap#...` URL은 최초 Admin을 만들 때 한 번만 사용한다. URL을 잃었거나 만료됐다면 Admin 생성 전에만 아래 명령으로 기존 Grant를 취소하고 새 URL을 발급할 수 있다.

```bash
uv run pangi bootstrap rotate --project-local --yes
```

Runtime을 시작한 뒤 Bootstrap URL에서 Admin을 만들고 `/login`에서 같은 계정으로 로그인한다. 기본 주소는 `http://127.0.0.1:8787`이다. 다른 Terminal에서는 다음 명령으로 상태를 확인한다.

```bash
uv run pangi status --project-local --json
```

Session은 기본 12시간 동안 유지된다. 생성 후 30분이 지나면 UI에서 Session을 명시적으로 회전할 수 있다. Loopback이 아닌 Host에서는 HTTPS가 아니면 로그인을 거부한다.

## 테스트와 검증

### Backend 전체 검증

아래 명령은 Lint, 정적 타입, 전체 Test와 OpenAPI Drift를 확인한다.

```bash
uv run ruff check .
uv run mypy src
uv run pytest
uv run python scripts/export_openapi.py --check
```

### Run 기능만 검증

WBS-05에서 지금까지 구현한 Domain, Schema, Idempotency, Cursor, Owner Scope와 Queue·복구를 빠르게 확인하려면 다음 Test를 실행한다.

```bash
uv run pytest \
  tests/unit/test_run_domain.py \
  tests/unit/test_run_service.py \
  tests/unit/test_run_queue_service.py \
  tests/integration/test_run_schema.py \
  tests/integration/test_run_persistence.py \
  tests/integration/test_run_queue_persistence.py
```

### Test 종류별 검증

```bash
uv run pytest tests/architecture tests/smoke
uv run pytest tests/contract
uv run pytest tests/integration
uv run pytest tests/unit
```

- `architecture`: Package 의존 방향과 Public API 경계를 검사한다.
- `smoke`: 설치 후 Import, CLI와 Package Resource를 검사한다.
- `contract`: CLI, Web 보안과 OpenAPI의 안정된 입출력 계약을 검사한다.
- `integration`: SQLite Transaction·Migration·인증·Runtime과 Run 영속성을 검사한다.
- `unit`: Config, Domain Policy와 Application Service를 외부 Runtime 없이 검사한다.

### Admin UI 검증

```bash
npm --prefix ui run check
npm --prefix ui run build
```

`check`는 OpenAPI 생성 Type과 TypeScript를 검사한다. `build`는 같은 검증을 수행한 뒤 배포용 정적 Asset을 `src/pangi/web/static`에 생성한다.
