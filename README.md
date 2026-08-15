# Pangi

조직이 직접 설치하고 운영하는 경량 Agent Runtime이다.

현재 개발 단계는 Pre-alpha다. WBS-01부터 WBS-05까지 완료했고 WBS-06을 진행 중이다. Run Core, 영속 Queue·복구, 조회·취소·Event 전달 API, 보호된 Input Guardrail, 중앙 Redaction·External Data Envelope와 공통 Tool Permission·Approval·Budget Guardrail까지 구현했다. 자연어 요청을 모델과 Tool로 실행하는 완성된 Agent Runtime은 아직 제공하지 않는다.

## 현재 구현 상태

| WBS | 상태 | 지금까지 구현한 범위 |
| --- | --- | --- |
| 01. Foundation과 Package 경계 | 완료 | Python 3.11+ wheel, 계층별 Package 경계, Public API, Plugin·Capability Pack 등록 기반 |
| 02. 설정·Runtime Data·CLI | 완료 | 설정과 경로 해석, `init`, `start`, `status`, `doctor`, `config`, `migrate`, Bootstrap 복구 명령 |
| 03. SQLite 영속성과 Migration | 완료 | 단일 Process Lock, 직렬화된 Unit of Work, Migration·Checksum·Backup, Snapshot 검증 |
| 04. Web/API Shell과 인증 | 완료 | FastAPI Runtime, React Admin Shell, Bootstrap Admin, Local Login·Session·CSRF·역할 검사, OpenAPI Type 동기화 |
| 05. Run 상태·Queue·Event | 완료 | Run/Step/Event 계약과 Schema, 생성·조회·Idempotency, Queue·Lease·복구, Owner 기반 API, Event JSON·SSE와 운영 Metric |
| 06. Guardrail·보안·Audit | 진행 중 | Input Guardrail 선행 Run 제출, Versioned 중앙 Redaction, 비신뢰 External Data Envelope, Tool Permission·Approval·Budget 공통 실행 경계 |
| 07~20 | 예정 | Model Routing, Orchestrator, MCP, Subagent, Skill, Scheduler, Slack, 관측성, 운영 배포 |

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

### Run Core, 영속 Queue와 Event API

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
- 인증된 Owner/Admin은 Run 목록과 상세를 조회하고, Same-origin·CSRF 검증 뒤 대기·실행 Run을 취소할 수 있다.
- Run Event는 JSON Page와 SSE로 조회한다. `Last-Event-ID` 재연결, Keepalive, Poll별 Session 재검증과 Terminal 종료를 지원한다.
- Owner는 Public Event만, Admin은 Public·Admin Event만 볼 수 있으며 Internal Event는 HTTP로 노출하지 않는다.
- Admin은 Queue 깊이, 실행 수, 만료 Lease 수와 가장 오래된 대기 시간을 식별자 없는 Metric으로 조회할 수 있다.
- OpenAPI와 Frontend 생성 Type, Run API Client와 `EventSource` Helper를 함께 제공한다.

Run 조회·취소·Event·Metric Service는 ASGI Composition Root에 연결됐다. 실제 실행 Handler와 Queue Runtime 조립, Run 생성 진입점, Run 화면은 후속 WBS에서 연결한다.

### Input Guardrail 기반

- 사용자 입력은 비신뢰 상태로 시작하며 활성 Principal과 요청 Principal의 사용자 ID·역할 일치를 먼저 검사한다.
- 본문은 CRLF/CR과 Unicode NFC를 정규화하고 UTF-8 Byte 기준 크기와 주입된 Control/Bidi/Hidden Unicode 정책을 적용한다. 탭·줄바꿈과 ZWJ 결합 Emoji는 보존한다.
- Attachment의 개수, 필수 크기·MIME Metadata, 개별·전체 Byte Limit과 허용 MIME을 검사한다.
- Explicit Skill 접근은 별도 Port로 검사한다. Skill 식별자·Version 형식은 아직 확정하지 않았다.
- 사용자·Channel별 요청률은 최대 Key 수가 제한된 단일 Process Sliding Window Adapter로 검사한다. 조직 운영 기본값은 아직 없다.
- Guardrail 통과 뒤에만 기존 Run 생성·SQLite Idempotency 경계를 호출한다. 차단 요청은 Run, Event와 Idempotency Record를 만들지 않는다.
- 판정에는 정책 Version·Fingerprint와 안전한 수치 Metadata만 남기고 요청 본문, Idempotency Key와 Attachment Reference는 포함하지 않는다.

이 경계는 아직 HTTP나 Channel의 Run 생성 진입점에 조립되지 않았다. WBS-08·11·16에서 Run을 수신할 때 보호된 제출 Service를 사용한다.

### 중앙 Redaction과 External Data

- `core-secret-v1` 정책이 Text와 중첩 JSON 호환 데이터의 Credential 할당, 알려진 Token Prefix, 민감 Key와 `secret://` Reference를 같은 방식으로 Redact한다.
- Redaction 결과는 정책 Version·Fingerprint, Redaction Count와 적용 Rule ID만 Metadata로 제공한다. 원문 값은 결과 표현에 포함하지 않는다.
- 기존 CLI Text·JSON 출력은 공개 함수 형태를 유지하면서 중앙 Redaction Service를 사용한다.
- External Data는 `text/plain` 또는 `text/html`로 받고 CRLF/NFC, Control/Bidi/Hidden Unicode와 실행·비가시 HTML을 정규화한다.
- 정규화된 외부 Content는 중앙 Redaction을 통과한 뒤 항상 `untrusted` Envelope로 생성된다. Content Fingerprint는 Redaction 완료 Text를 기준으로 계산한다.
- Prompt Renderer는 Source와 Content를 Escape하므로 외부 Text가 `external_data`를 닫거나 새 System Tag를 만들 수 없다.

이 기반은 아직 MCP, Web Fetch, Model Provider, Log·Run Event와 최종 Output에 연결되지 않았다. 각 실행 경계는 후속 WBS에서 중앙 Service와 Envelope를 사용한다.

### Tool Permission·Approval·Budget 기반

- Stable Tool ID를 현재 Connection과 Schema Snapshot으로 해석하는 Port를 제공한다. Stable ID의 실제 저장 형식과 MCP Tool Name Mapping은 아직 구현하지 않았다.
- 활성 Principal과 Run 요청자의 사용자 ID를 먼저 비교하고 User Connection Owner를 다시 검사한다. 다른 사용자의 Run이나 Connection을 실행하지 않으며 Instance Connection에는 사용자 Owner를 허용하지 않는다.
- 명시적인 Tool Policy가 없으면 기본 Deny한다. Policy는 Connection, Schema Fingerprint, `read`·`write`·`destructive` Permission과 `none`·`user`·`admin` Approval에 정확히 묶인다.
- Argument를 Canonical JSON으로 고정하고 UTF-8 Byte Limit과 주입된 JSON Schema Validator를 통과시킨다. 호출 뒤 원본 Mapping이 바뀌어도 실행 Argument는 변하지 않는다.
- Approval은 Actor, Run, Tool, Argument와 Policy Fingerprint에 묶고 만료와 승인 주체를 검증한다.
- Run·Tool별 호출 횟수는 정책 Version이 바뀌어도 유지하며, 실행 실패도 예약된 호출 횟수를 소비한다.
- 모든 검사를 통과한 `GuardedToolCall`만 Executor에 전달한다. 차단 호출은 Executor를 호출하지 않으며 Timeout과 Result Byte Limit은 허용된 호출에 필수로 전달한다.
- 판정과 오류에는 정책 Fingerprint와 안전한 수치만 남기고 Argument, Approval Reference, Connection ID·Owner와 실제 Tool Name을 표현하지 않는다.

실제 MCP Registry·Transport, JSON Schema Adapter, Policy·Approval·Invocation 저장소와 Result 정규화는 WBS-09에서 연결한다. 현재 구현은 Tool 호출을 수행하는 기능이 아니라 후속 실행기가 반드시 거쳐야 하는 공통 보안 경계다.

## 아직 구현되지 않은 기능

- Root Orchestrator와 실제 실행 Handler의 Queue Runtime 연결, Lease·Heartbeat 운영 기본값
- Input Guardrail을 사용하는 Run 생성 진입점과 Run Timeline·Workflow Admin UI
- 실제 MCP Tool Registry·실행 Adapter와 Policy·Approval·Budget 영속화
- Output·Log·Event Guardrail과 Append-only Audit
- Model Provider Routing과 데이터 반출 정책
- Root Orchestrator, 실행 Engine, MCP, Subagent와 Web Search
- Skill, Workflow UI, Memory, Scheduler와 Eval
- Slack 요청 수신과 응답 전달
- Analytics, Feedback, API Key·IP Policy와 운영 Upgrade/Rollback

따라서 현재 Runtime은 설치·저장소·인증·Admin Shell, Run 영속성·Queue 복구와 Event 전달 기반을 검증하는 단계다. 실제 업무 요청을 처리하는 Agent 기능은 후속 WBS에서 연결한다.

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

WBS-05에서 구현한 Domain, Schema, Idempotency, Cursor, Owner Scope, Queue·복구와 Event API를 빠르게 확인하려면 다음 Test를 실행한다.

```bash
uv run pytest \
  tests/unit/test_run_domain.py \
  tests/unit/test_run_service.py \
  tests/unit/test_run_queue_service.py \
  tests/contract/test_run_web_contract.py \
  tests/integration/test_run_schema.py \
  tests/integration/test_run_persistence.py \
  tests/integration/test_run_queue_persistence.py \
  tests/integration/test_run_event_delivery.py
```

### Input Guardrail만 검증

WBS-06.1에서 구현한 신뢰 계약, 입력 정규화, Principal·Attachment·Explicit Skill·Rate Limit 검사와 Guardrail 선행 Run 영속화 경계는 다음 명령으로 확인한다.

```bash
uv run pytest \
  tests/unit/test_input_guardrails.py \
  tests/integration/test_guarded_run_submission.py \
  tests/architecture/test_dependency_rules.py
```

### 중앙 Redaction과 External Data만 검증

WBS-06.2에서 구현한 Versioned Redaction, External Text·HTML 정규화, 비신뢰 Envelope와 경계 Escape를 확인한다.

```bash
uv run pytest \
  tests/unit/test_redaction_service.py \
  tests/unit/test_external_data_service.py \
  tests/unit/test_output.py \
  tests/architecture/test_dependency_rules.py
```

### Tool Guardrail만 검증

WBS-06.3에서 구현한 Stable Tool ID, Connection Owner, Permission·Schema, Approval, Call·Byte·Timeout Budget과 강제 실행 경계를 확인한다.

```bash
uv run pytest \
  tests/unit/test_tool_guardrails.py \
  tests/architecture/test_dependency_rules.py
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
