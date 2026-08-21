# Pangi

Pangi는 조직이 직접 설치하고 운영하는 경량 Agent Runtime이에요.

현재 개발 단계는 Pre-alpha예요. WBS-01부터 WBS-05까지와 WBS-07·08을 완료했고 WBS-06·09를 진행하고 있어요. Run Core, 영속 Queue·복구와 조회·취소·Event 전달 API를 구현했어요. 보호된 Input Guardrail부터 Append-only Audit까지 공통 보안 기반도 마련했어요. OpenAI·Bedrock 선택 설치 Adapter, Model Retry 계약, 안전한 Policy·Invocation 영속화, 관리자용 Policy 조회·활성화 Gate API와 Dashboard를 추가했어요. 인증된 Run 생성 요청은 이제 Guardrail, Data Class 기반 Root Decision, 영속 Queue와 안전한 Output 완료까지 하나의 실제 Runtime 경로로 처리해요. MCP 연결 구현을 시작해 Connection·Tool Registry 계약과 Lifecycle 상태기계를 추가했어요.

## 현재 구현 상태

| WBS | 상태 | 지금까지 구현한 범위 |
| --- | --- | --- |
| 01. Foundation과 Package 경계 | 완료 | Python 3.11+ wheel, 계층별 Package 경계, Public API, Plugin·Capability Pack 등록 기반 |
| 02. 설정·Runtime Data·CLI | 완료 | 설정과 경로 해석, `init`, `start`, `status`, `doctor`, `config`, `migrate`, Bootstrap 복구 명령 |
| 03. SQLite 영속성과 Migration | 완료 | 단일 Process Lock, 직렬화된 Unit of Work, Migration·Checksum·Backup, Snapshot 검증 |
| 04. Web/API Shell과 인증 | 완료 | FastAPI Runtime, React Admin Shell, Bootstrap Admin, Local Login·Session·CSRF·역할 검사, OpenAPI Type 동기화 |
| 05. Run 상태·Queue·Event | 완료 | Run/Step/Event 계약과 Schema, 생성·조회·Idempotency, Queue·Lease·복구, Owner 기반 API, Event JSON·SSE와 운영 Metric |
| 06. Guardrail·보안·Audit | 진행 중 | Input Guardrail 선행 Run 제출, Versioned 중앙 Redaction, 비신뢰 External Data Envelope, Tool Permission·Approval·Budget, 최종 Output·Log·Run Event Redaction, Append-only Audit, 보안 정책 영향 Fingerprint |
| 07. Model Routing과 Egress Policy | 완료 | Model 계약, Versioned Profile·Egress Policy, Data Class·Redaction 경계, OpenAI·Bedrock 선택 설치 Adapter, 구조화 출력 검증·Transport Retry, Policy·Invocation 영속화·계측, 관리자 조회·영향 분석·실패 폐쇄 Eval 활성화 Gate API와 읽기 전용 Dashboard |
| 08. Root Orchestrator와 실행 Engine | 완료 | 보호된 Run 생성 API, Data Class 기반 단일 Root 호출, Plan 검증·영속화, Queue Runtime·ASGI 생명주기, Dependency 실행·복구, 결정적 Reducer와 `SafeOutput` 완료 |
| 09. MCP 연결과 Tool Policy | 진행 중 | Connection·Tool Registry 계약, User/Instance Scope, Lifecycle 상태기계, 제한된 Canonical Tool Schema Fingerprint |
| 10~20 | 예정 | Subagent, Skill, Scheduler, Slack, 관측성, 운영 배포 |

전체 작업 순서와 완료 조건은 [Pangi 1.0 구현 WBS](docs/chunks/README.md)에서 관리해요. 구현 결정과 전체 구조는 [Pangi 1.0 재설계 구현 설계서](docs/pangi-rebuild-implementation-design.md)에서 확인할 수 있어요.

## 구현된 기능

### Package와 확장 경계

- Domain, Application, Adapter의 의존 방향을 Architecture Test로 검사해요.
- `PangiRuntime`과 필요한 Domain 계약만 Package Root의 Public API로 노출해요.
- Provider, Channel, Secret Store, Subagent를 Python Entry Point로 발견할 수 있어요.
- Built-in Resource, 선택 설치 Capability Pack, UI 정적 Asset과 Runtime Data를 분리해요.

### CLI와 Runtime

- OS 기본 경로와 프로젝트 로컬 `.pangi/` 경로를 지원해요.
- `pangi init`은 설정과 Runtime Directory를 만들고 SQLite Migration을 적용해요.
- `pangi start`는 SQLite와 FastAPI/Uvicorn Runtime을 하나의 Process로 실행해요.
- `pangi status`는 열린 Port가 아니라 Pangi의 Live 응답을 확인해요.
- `pangi doctor`는 경로, 설정, SQLite, Process와 Package 상태를 읽기 전용으로 진단해요.
- `pangi migrate plan|apply`는 Migration 계획, Checksum과 사전 Backup을 관리해요.

### SQLite 영속성

- Process File Lock으로 같은 Runtime Data를 두 Process가 동시에 열지 못하게 해요.
- 하나의 Connection과 직렬화된 Unit of Work로 Commit과 Rollback 경계를 강제해요.
- Migration Registry는 순서와 Checksum을 검증하고 실패한 Migration 전체를 Rollback해요.
- DB Snapshot과 Manifest의 Hash, `quick_check`, 경로 안전성을 검증해요.

### Web Shell과 Local 인증

- `/health/live`와 `/health/ready`는 Runtime 상태를 구분해 반환해요.
- 일회성 Bootstrap URL로 최초 Local Admin을 생성해요. 원문 Token은 DB에 저장하지 않아요.
- Local Login, Session 확인·회전·Logout과 비활성 사용자 차단을 지원해요.
- Same-origin, CSRF, Secure Cookie와 역할 기반 API Dependency를 적용해요.
- React/Vite Admin Shell과 역할별 Navigation을 wheel 정적 Asset으로 제공해요.
- Backend OpenAPI와 Frontend Type의 변경 불일치를 검증해요.

### Run Core, 영속 Queue와 Event API

- `Principal`, `RunRequest`, `Run`, `RunStep`, `RunEvent`와 상태 전이 규칙을 제공해요.
- Run, 첫 `run.received` Event와 Idempotency 결과를 하나의 Transaction으로 저장해요.
- 같은 Principal·Route·Idempotency Key로 정확하게 재시도하면 기존 Run을 반환해요.
- 다른 요청이 같은 Idempotency Key를 사용하면 충돌로 거부해요. 기본 보존 시간은 24시간이에요.
- `(created_at DESC, id DESC)` Keyset Cursor로 Run 목록을 조회해요.
- Member, Skill Author와 System은 자신이 소유한 Run만 조회해요. Admin은 전체 Run을 조회할 수 있어요.
- 목록은 Metadata만 반환해요. 상세 조회는 Owner 검사를 통과한 뒤 정규화된 요청을 복원해요.
- `queued_at`, `created_at`, `id` 순서로 가장 오래된 Run을 `BEGIN IMMEDIATE` Transaction에서 한 번만 Claim해요.
- Claim과 Queue 상태 변경에는 Revision CAS를 사용해요. Heartbeat는 현재 Worker와 유효 Lease가 일치할 때만 갱신해요.
- Process-local Queue Runtime은 `asyncio.Event`로 깨어나고 `max_concurrent_runs` Semaphore로 동시 실행 수를 제한해요.
- 대기·실행 Run 취소와 오래된 Worker의 쓰기 거부를 지원해요.
- 재시작하면 만료된 Run을 복구해요. 실행 중인 Step이 없거나 모두 Idempotent라면 다시 대기 상태로 전환하고, Non-idempotent Step이 있으면 안전하게 실패시켜요.
- Queue 상태와 `run.queued`, `run.running`, `run.interrupted`, `run.cancelled`, `run.failed` Event를 같은 Transaction에 저장해요.
- 인증된 Owner/Admin은 Run 목록과 상세를 조회할 수 있어요. Same-origin·CSRF 검증을 통과하면 대기·실행 Run을 취소할 수 있어요.
- Run Event는 JSON Page와 SSE로 조회해요. `Last-Event-ID` 재연결, Keepalive, Poll별 Session 재검증과 Terminal 종료를 지원해요.
- Owner는 Public Event만, Admin은 Public·Admin Event만 볼 수 있어요. Internal Event는 HTTP로 노출하지 않아요.
- Admin은 Queue 깊이, 실행 수, 만료 Lease 수와 가장 오래된 대기 시간을 식별자 없는 Metric으로 조회할 수 있어요.
- OpenAPI와 Frontend 생성 Type, Run API Client와 `EventSource` Helper를 함께 제공해요.

Run 조회·생성·취소·Event·Metric Service와 실제 실행 Handler는 ASGI Composition Root에 연결됐어요. Startup은 SQLite 다음 Queue 복구·Dispatcher 순서로 진행하고 Shutdown은 역순으로 정리해요. Queue Dispatcher가 중단되면 Readiness도 `not_ready`로 바뀌어요. Run Timeline·Workflow 화면은 후속 WBS에서 연결해요.

### Input Guardrail 기반

- 사용자 입력은 비신뢰 상태로 시작해요. 먼저 활성 Principal과 요청 Principal의 사용자 ID·역할이 일치하는지 검사해요.
- 본문은 CRLF/CR과 Unicode NFC를 정규화하고 UTF-8 Byte 기준 크기와 주입된 Control/Bidi/Hidden Unicode 정책을 적용해요. 탭·줄바꿈과 ZWJ 결합 Emoji는 보존해요.
- Attachment의 개수, 필수 크기·MIME Metadata, 개별·전체 Byte Limit과 허용 MIME을 검사해요.
- Explicit Skill 접근은 별도 Port로 검사해요. Skill 식별자·Version 형식은 아직 확정하지 않았어요.
- 사용자·Channel별 요청률은 최대 Key 수가 제한된 단일 Process Sliding Window Adapter로 검사해요. 현재 Local Dashboard Baseline은 분당 60건이에요.
- Guardrail을 통과한 뒤에만 기존 Run 생성·SQLite Idempotency 경계를 호출해요. 차단된 요청은 Run, Event와 Idempotency Record를 만들지 않아요.
- 판정에는 정책 Version·Fingerprint와 안전한 수치 Metadata만 남겨요. 요청 본문, Idempotency Key와 Attachment Reference는 포함하지 않아요.

`POST /api/v1/runs`는 로그인 Session, Same-origin, CSRF와 `Idempotency-Key`를 확인한 뒤 이 경계를 사용해요. 초기 계약은 Text, Thread와 선택적인 Explicit Skill만 받고 Attachment는 안전한 Upload 경계가 생길 때까지 허용하지 않아요. Explicit Skill은 Registry가 구현되는 WBS-11 전까지 실패 폐쇄해요.

### 중앙 Redaction과 External Data

- `core-secret-v1` 정책은 Text와 중첩 JSON 호환 데이터의 Credential 할당, 알려진 Token Prefix, 민감 Key와 `secret://` Reference를 같은 방식으로 Redact해요.
- Redaction 결과는 정책 Version·Fingerprint, Redaction Count와 적용 Rule ID만 Metadata로 제공해요. 원문 값은 결과 표현에 포함하지 않아요.
- 기존 CLI Text·JSON 출력은 공개 함수 형태를 유지하면서 중앙 Redaction Service를 사용해요.
- External Data는 `text/plain` 또는 `text/html`로 받아요. CRLF/NFC, Control/Bidi/Hidden Unicode와 실행·비가시 HTML을 정규화해요.
- 정규화한 외부 Content는 중앙 Redaction을 통과한 뒤 항상 `untrusted` Envelope로 생성돼요. Content Fingerprint는 Redaction을 마친 Text를 기준으로 계산해요.
- Prompt Renderer는 Source와 Content를 Escape해요. 따라서 외부 Text는 `external_data`를 닫거나 새 System Tag를 만들 수 없어요.

이 기반은 Log·Run Event에 연결됐어요. MCP, Web Fetch와 Model Provider의 실제 입출력 경계는 후속 WBS에서 중앙 Service와 Envelope를 사용해요.

### Tool Permission·Approval·Budget 기반

- Stable Tool ID를 현재 Connection과 Schema Snapshot으로 해석하는 Port를 제공해요. Connection과 Tool Snapshot은 SQLite에 저장하고 Stable Tool ID는 Registry 전체에서 유일하게 관리해요. 실제 MCP Tool Discovery와 원격 이름 Mapping은 아직 연결하지 않았어요.
- 먼저 활성 Principal과 Run 요청자의 사용자 ID를 비교하고 User Connection Owner를 다시 검사해요. 다른 사용자의 Run이나 Connection을 실행하지 않으며 Instance Connection에는 사용자 Owner를 허용하지 않아요.
- 명시적인 Tool Policy가 없으면 기본으로 Deny해요. Policy는 Connection, Schema Fingerprint, `read`·`write`·`destructive` Permission과 `none`·`user`·`admin` Approval에 정확히 묶여요.
- Argument를 Canonical JSON으로 고정하고 UTF-8 Byte Limit과 주입된 JSON Schema Validator를 통과시켜요. 호출 뒤 원본 Mapping이 바뀌어도 실행 Argument는 변하지 않아요.
- Approval은 Actor, Run, Tool, Argument와 Policy Fingerprint에 묶고 만료와 승인 주체를 검증해요.
- Run·Tool별 호출 횟수는 정책 Version이 바뀌어도 유지해요. 실행에 실패해도 예약된 호출 횟수를 소비해요.
- 모든 검사를 통과한 `GuardedToolCall`만 Executor에 전달해요. 차단된 호출은 Executor로 보내지 않으며, Timeout과 Result Byte Limit은 허용된 호출에 반드시 전달해요.
- 판정과 오류에는 정책 Fingerprint와 안전한 수치만 남겨요. Argument, Approval Reference, Connection ID·Owner와 실제 Tool Name은 표현하지 않아요.

Connection·Tool Registry의 SQLite Schema와 Repository, Revision Compare-and-Swap, 오래된 Discovery 거부와 Stable Resolver는 WBS-09.2.1에서 연결했어요. Tool이 `active`이고 Connection이 `connected`일 때만 Resolver가 실행 가능 상태로 반환해요. 실제 MCP Transport·Discovery, JSON Schema Adapter, Policy·Approval·Budget·Invocation 저장소와 Result 정규화는 후속 WBS에서 연결해요. 현재 구현은 아직 Tool 호출 기능이 아니라 후속 실행기가 반드시 거쳐야 하는 공통 보안 경계예요.

### 최종 Output Guardrail 기반

- WBS-08의 Direct Answer·Reducer 결과를 위한 Framework-free `OutputCandidate`→`SafeOutput` 경계를 제공해요. 모델 출력은 항상 `untrusted`로 유지해요.
- CRLF/NFC 정규화와 전체 입력 UTF-8 Byte Limit 뒤에 중앙 `core-secret-v1` Redaction을 Markdown·Evidence에 함께 적용해요.
- Python·Node Stack Trace와 Unix·Windows 내부 Path를 Versioned Rule로 제거하고 Raw HTML과 Slack Angle Markup을 Escape해요.
- Markdown Inline·Reference Link와 Evidence Link에 같은 Scheme 정책을 적용해요. 허용하지 않은 Scheme과 Protocol-relative Link는 제거하고 Inline Link Label은 보존해요.
- Broadcast Mention은 항상 중립화하고 일반 Mention은 명시된 개수를 넘긴 항목만 중립화해요. 최종 길이는 한국어·Emoji Codepoint를 깨뜨리지 않고 UTF-8 Byte 기준으로 잘라요.
- 허용된 결과에는 Sanitized Content Fingerprint, 정책 Version·Fingerprint와 변경 수치만 제공해요. 원문 Output·Evidence와 Rule 본문은 오류·`repr`에서 제외해요.

이 경계는 Root Orchestrator의 Direct·Delegate 합성과 영속 `run_outputs` 저장에 연결됐어요. WBS-16의 Slack Renderer도 공통 Guardrail을 우회하지 않고 `SafeOutput`만 받아야 해요.

### Log와 Run Event Redaction

- `core-telemetry-v1` 정책은 Log·Event Message와 구조화 데이터의 UTF-8 Byte, 재귀 깊이·항목 수, 허용 Log Field를 명시해요.
- Telemetry Payload는 CRLF/NFC 정규화 뒤에 중앙 `core-secret-v1` Redaction을 통과해요. 결과와 오류 표현에는 원문 Secret을 남기지 않아요.
- Logging Filter는 `%` Argument를 한 번만 렌더링하고 Message와 허용 Extra를 Redact해요. 임의 Extra, Exception 원문과 Stack은 제거하고 Exception Type만 보존해요.
- 첫 `run.received`, Queue 상태 Event와 범용 Append는 하나의 SQLite 최종 Writer를 사용해요. 실패하면 같은 Transaction의 상태 변경과 Event를 함께 Rollback해요.
- Run Event는 금지 Field를 재귀적으로 거부해요. 저장된 Safe Event만 기존 JSON API와 SSE를 통해 전달해요.

JSON Log Formatter, Metric, Trace와 선택형 OpenTelemetry는 WBS-17에서 구현해요. 기존 Event Schema와 HTTP/OpenAPI 계약은 바꾸지 않았어요.

### Append-only Audit 기반

- `core-audit-v1`은 Actor, Action, Resource, Outcome과 이전·이후 Summary를 정규화하고 기존 `core-secret-v1`으로 Redact해요.
- 안전한 이전·이후 Summary, Details와 전체 변경에 Canonical SHA-256 Fingerprint를 남겨요. 원문 관리 Payload는 오류와 객체 표현에 포함하지 않아요.
- SQLite Migration 4는 `audit_events`와 조회 Index를 추가해요. Event `UPDATE`와 365일 Retention 이전 `DELETE`는 DB Trigger가 거부해요.
- Bootstrap Grant 발급·회전, 최초 Admin 생성과 Migration 적용은 상태 변경과 Audit Insert를 같은 Transaction에 저장해요.
- `GET /api/v1/audit-events`는 활성 Admin만 호출할 수 있어요. Actor·Action·Resource·Outcome·기간 Filter와 Keyset Cursor를 지원해요.
- Audit Actor는 `users` Foreign Key에 묶지 않아요. 사용자 상태가 바뀌거나 System Actor가 기록해도 기존 Event를 보존해요.

아직 구현하지 않은 Tool Policy·Skill·Memory·Schedule·API Key·IP Policy 같은 관리 Action은 각 기능 WBS에서 같은 Audit 경계에 연결해요. React Audit Log 화면과 Retention Job은 후속 WBS에서 구현해요.

### 보안 정책 영향 Fingerprint와 신뢰 경계

- `policy-impact-v1`은 Policy Kind·Stable ID·Version·기존 SHA-256 Fingerprint만 불변 참조로 받아요.
- `PolicyImpactSnapshot`은 참조 순서와 관계없이 같은 정책 집합에 같은 영향 Fingerprint를 만들어요.
- Baseline과 Candidate를 비교하면 추가·삭제·변경된 Policy Key를 구분할 수 있어요.
- Policy 원문, Rule, Connection 정보, Secret과 외부 Content는 Snapshot과 Diff에 포함하지 않아요.
- 외부 문서가 Envelope를 닫거나 System Tag를 만들려고 해도 해당 구문을 Escape하고 `untrusted` 상태를 유지해요.
- 외부 문서가 Tool 실행을 지시해도 명시 Policy가 없거나 Deny이면 Executor 전에 차단해요.

실제 영향 Eval Suite 선택·실행·활성화 Gate와 Snapshot 영속화는 WBS-15에서 구현해요. Web Search·MCP Result의 End-to-End Prompt Injection 검증은 WBS-10·15에서 연결해요.

### Model Routing과 Egress Policy 기반

- 모든 Model 요청은 논리 Profile, 목적, Source Kind, Data Class와 구조화 Output Schema를 명시해요.
- Data Class는 `public`, `internal`, `confidential`, `personal`, `restricted` 순서로 더 민감해져요. 여러 Source가 있으면 전체 Class와 최고 등급을 함께 계산해요.
- Versioned Model Profile은 Provider, Model, Region, 지원 Class·Source·목적, Retention, Raw Content와 명시적 후보 Priority를 고정해요.
- Versioned Egress Policy는 허용 Provider·Model·Region·Class·Source·목적과 Redaction·Zero-retention·Raw Content 조건을 검사해요.
- 후보는 요청의 모든 Data Class와 Source Kind를 지원해야 해요. 후보가 없거나 후보 ID·Priority가 중복되면 Provider를 호출하지 않고 `model_policy_denied`로 실패해요.
- 허용된 입력도 중앙 Redaction을 항상 통과해요. Provider Port에는 Redaction 완료 Content와 안전한 Fingerprint만 전달해요.
- Model Profile과 Egress Policy Fingerprint를 기존 Policy Impact Snapshot에 포함할 수 있어요.
- System과 User 입력 역할을 공통 계약으로 구분해요. 두 Provider Adapter는 같은 Source 순서를 유지해요.
- OpenAI Adapter는 Responses API의 Strict JSON Schema를 사용하고 응답 저장을 요청하지 않아요. Bedrock Adapter는 Converse API의 구조화 출력을 사용해요.
- `pangi-agent[openai]`와 `pangi-agent[bedrock]`을 선택해서 Provider SDK를 설치해요. 기본 Package Import는 선택 SDK를 불러오지 않아요.
- SDK 자체 Retry를 끄고 Pangi가 Timeout, Rate Limit과 일시적인 서버 오류만 재시도해요. 하나의 Logical Call에서 발생한 실제 Provider Request 수를 따로 반환해요.
- Provider 응답은 요청한 JSON Schema로 다시 검증해요. JSON이나 Schema가 잘못되면 Semantic Retry 없이 안전하게 실패해요.
- Token Usage, 전체 Duration, Provider Latency와 종료 사유를 공통 응답 계약으로 변환해요.
- SQLite Migration 5는 Versioned Model Policy Snapshot과 Model Invocation을 저장해요. 같은 Policy 이름에는 Active Version을 하나만 허용하고 Active 규칙의 변경·삭제를 거부해요.
- SQLite Migration 6은 신규 Model Invocation에 요청한 논리 Profile을 기록해요. 최근 허용·거부 호출을 Profile별로 안전하게 집계할 수 있어요.
- 허용된 Logical Call은 Provider를 호출하기 전에 `running` Invocation과 `model.policy_allowed` 내부 Event를 저장해요. 저장에 실패하면 Provider를 호출하지 않아요.
- Policy가 호출을 차단하면 Provider 요청 없이 `denied` Invocation과 `model.policy_denied` 내부 Event를 같은 Transaction에 저장해요.
- Provider 호출이 끝나면 Token, Duration, Provider Request 수와 성공·실패 상태를 `model.invocation_completed` 내부 Event와 함께 저장해요. Provider Network 요청 중에는 SQLite Transaction을 유지하지 않아요.
- 원문 Logical Call ID는 저장하지 않고 Fingerprint만 남겨요. Prompt, Redaction 이후 실제 입력, 구조화 Model Output과 Credential은 SQLite와 Run Event에 저장하지 않아요.
- 같은 Run에서 같은 Logical Call을 다시 실행하면 Provider 호출 전에 거부해요. 영속화 실패 때문에 Provider를 다시 호출하지도 않아요.
- Prompt, Output Schema와 구조화 Provider Output은 결과·오류·객체 표현에 포함하지 않아요.
- `GET /api/v1/model-policies`는 관리자에게 Version별 상태, Egress/Profile Summary, 최근 7일 허용·거부·목적·거부 이유와 Candidate 영향 Fingerprint를 제공해요.
- Policy 평가와 활성화 API는 `(policy_id, version)`을 경로에 명시하고 Same-origin·CSRF·관리자 권한을 검사해요. 활성화는 `Idempotency-Key`, Candidate·Impact Fingerprint와 Eval Run ID가 모두 일치해야 해요.
- 승인된 활성화는 기존 Active 폐기, Candidate 활성화, Eval Run 연결, Audit와 Idempotency 결과를 하나의 Transaction으로 저장해요. 중간에 실패하면 모두 Rollback해요.
- 실제 Eval Suite 선택·실행은 WBS-15 범위예요. 현재 Runtime은 실패 폐쇄 Gateway를 사용하므로 WBS-15가 연결되기 전에는 Eval 요청과 활성화를 허용하지 않아요.
- 관리자 전용 `/model-policies` 화면에서 Policy Version 상태, Egress 허용 범위와 물리 Model Profile을 확인할 수 있어요.
- 최근 7일 허용·거부 횟수, Purpose, 거부 사유와 Draft 변경 영향을 읽기 전용으로 확인할 수 있어요.
- Consumer와 필수 Eval Suite가 아직 연결되지 않은 상태를 실제 빈 목록과 구분해 안내해요.
- Model Policy 목록은 Cursor로 다음 페이지를 이어서 조회해요. 로딩, 빈 목록, 오류, 권한 없음과 모바일 화면 상태를 각각 처리해요.
- 활성 Model Policy가 선택한 `openai` 또는 `bedrock` Adapter만 지연 생성해요. 선택되지 않은 SDK는 불러오지 않고 Provider 실패를 이유로 다른 Provider에 임의로 Fallback하지 않아요.
- `[model]` 설정은 Root Profile과 Provider Retry·Timeout만 관리해요. OpenAI API Key와 AWS Credential은 `pangi.toml`에 저장하지 않아요.
- SQLite 활성 Policy, 중앙 Redaction, 구조화 출력 검증, Invocation 기록과 선택 Provider를 하나의 Root Model 실행 경계로 조립해요.

Model Policy 생성·Eval 실행과 활성화 운영 경로, 사용처 Registry는 후속 단계에서 구현해요. 활성 Policy가 없으면 Provider를 만들거나 Network를 호출하지 않고 요청을 거부해요.

### Root Orchestrator와 실행 Engine 기반

- Direct, Delegate와 Skill Decision 계약과 엄격한 XOR·DAG·Registry·Task 수·Timeout 검증을 제공해요.
- Root Context는 Principal 범위의 Subagent·Skill·Connection 최소 Catalog Snapshot과 정규화된 사용자 Data만 포함해요. 일반 자연어는 Root Model을 정확히 한 번 호출하고 명시 Skill은 호출하지 않아요.
- 검증된 Plan과 Canonical Task·Redaction 완료 `AgentResult`를 SQLite에 저장해요. 같은 Plan Replay는 허용하고 다른 Plan 덮어쓰기는 거부해요.
- Dependency가 완료된 Step만 Plan 순서와 주입된 동시 실행 상한 안에서 실행해요. Required·Optional 실패, Timeout, 취소, Lease 소유권과 Idempotent 복구 규칙을 적용해요.
- Reducer는 Result 입력 순서와 관계없이 Plan 순서로 Summary, Warning과 Evidence를 구성해요. Evidence URI 중복은 첫 항목만 유지해요.
- Synthesis Mode는 최초 DAG의 Terminal Synthesis Result만 본문으로 사용하며 합성 중 Model이나 Subagent를 다시 호출하지 않아요.
- Direct Answer와 Delegate 결과는 같은 Output Guardrail을 통과해요. 이후 경계에는 Secret·내부 경로·위험 Link·Mention을 처리한 `SafeOutput`만 제공해요.
- Guardrail을 통과해 생성된 Run은 `planning`에서 Root Decision을 한 번 수행하고, 성공한 Direct·Delegate Plan만 영속 Queue로 넘겨요. Decision과 Validation 실패는 외부 실행 전에 `failed`로 종료해요.
- Queue Handler는 실행 결과를 결정적으로 합성하고 `SafeOutput` 저장, Output Event와 `composing → completed|failed` 전이를 하나의 SQLite Transaction으로 처리해요.
- `composing` 중에도 Worker Lease와 Heartbeat를 유지해요. Handler 종료나 Lease 만료가 발생하면 Output이나 Root Decision을 다시 실행하지 않고 `composition_interrupted`로 실패시켜요.
- Root Runtime은 Principal을 받는 불변 Catalog 경계를 사용해요. Subagent·Skill·Connection Registry가 구현되기 전에는 유효한 빈 Snapshot을 반환하므로 Direct만 가능하고 알 수 없는 Delegate·Skill은 외부 실행 전에 차단해요.
- Root Composition Factory는 SQLite 활성 Model Policy와 Invocation 저장소, 선택 Provider, JSON Schema Validator와 빈 Catalog를 연결해요.
- `POST /api/v1/runs`는 Principal·Request ID·생성 시각과 Data Class를 서버에서만 결정해요. 클라이언트는 이 Metadata를 제출할 수 없어요.
- `runtime.run_data_classes`는 Root에 전달할 신뢰된 분류 집합이에요. 기존 설정에는 과소 분류를 피하기 위해 `restricted`를 기본값으로 적용해요.
- Guardrail을 통과한 Run은 Root Planning과 Plan·Queue Commit을 마친 뒤 Process-local Dispatcher를 깨워요. 정확한 Idempotency Replay는 Root를 다시 호출하지 않아요.
- Queue Runtime은 ASGI와 함께 시작·종료하고 Dispatcher 상태를 Readiness에 반영해요. 취소는 DB 상태를 먼저 확정한 뒤 같은 Process의 활성 실행 Task에도 신호를 전달해요.

현재 Root Catalog는 존재하지 않는 Capability를 만들지 않는 빈 Snapshot을 사용해요. 따라서 실제 Runtime은 Direct 요청만 완료할 수 있고 Delegate·Skill은 WBS-09~11의 Registry와 실행기가 연결될 때까지 실패 폐쇄해요.

## 아직 구현되지 않은 기능

- Attachment Upload와 Run Timeline·Workflow Admin UI
- 실제 MCP Tool Registry·실행 Adapter와 Policy·Approval·Budget 영속화
- Model Policy 생성·초기 활성화 운영 경로, 사용처 Registry와 WBS-15 Eval 실행기 연결
- Subagent와 Web Search
- Skill, Workflow UI, Memory, Scheduler와 Eval
- Slack 요청 수신과 응답 전달
- 미래 관리 Action의 Audit 연결과 Audit Log 화면
- JSON Log Formatter·Metric·Trace와 선택형 OpenTelemetry
- Analytics, Feedback, API Key·IP Policy와 운영 Upgrade/Rollback

현재 Runtime은 인증된 Text 요청을 Direct Root 응답으로 처리하고 안전한 Output으로 저장할 수 있어요. 실제 MCP Tool, Subagent, Skill과 외부 Channel을 사용하는 업무 요청은 후속 WBS에서 연결해요.

## 개발 환경 준비

Python 3.11 이상과 `uv`가 필요해요. Backend 개발 의존성은 잠금 파일에 맞춰 설치하세요.

```bash
uv sync --extra dev --python 3.11
```

Root Model Runtime을 사용하려면 선택한 Provider Extra를 설치하세요. Runtime은 활성 Policy가 선택한 Provider만 지연 생성해요.

```bash
# OpenAI Adapter와 개발 의존성을 함께 설치해요.
uv sync --extra dev --extra openai --python 3.11

# Bedrock Adapter와 개발 의존성을 함께 설치해요.
uv sync --extra dev --extra bedrock --python 3.11
```

OpenAI는 SDK 표준 환경변수인 `OPENAI_API_KEY`를 사용해요. Bedrock은 AWS Credential Chain과 활성 Model Profile의 Region을 사용해요. Credential은 `.pangi/pangi.toml`, 로그와 Run Event에 저장하지 마세요.

`pangi init`이 만드는 `[runtime]` 설정의 `run_data_classes`는 HTTP 본문이 아니라 신뢰된 서버 정책이에요. 기본 `restricted`를 낮추려면 실제 입력과 Provider 반출 정책을 검토한 뒤 명시적으로 변경하세요.

```toml
[runtime]
max_concurrent_runs = 4
max_subagents_per_run = 3
run_timeout_seconds = 180
run_data_classes = ["restricted"]
```

`[model]` 설정에는 비밀값이 없어요. `root_profile`은 SQLite의 활성 Model Policy 이름과 같아야 해요. Retry는 하나의 Root Logical Call 안에서 발생하는 실제 Provider 요청만 늘려요.

```toml
[model]
root_profile = "root-default"
max_attempts = 3
attempt_timeout_seconds = 30.0
total_timeout_seconds = 90.0
retry_backoff_seconds = [0.5, 1.0]
```

활성 Model Policy가 없거나 선택한 Provider Extra가 설치되지 않으면 Root Model 실행은 실패 폐쇄해요. 다른 Provider로 자동 전환하지 않아요.

Admin UI를 수정하거나 검증하려면 Node.js와 npm도 준비하세요.

```bash
npm --prefix ui ci
```

## 로컬에서 실행하기

아래 예시는 Runtime Data를 저장소의 `.pangi/`에 만드는 프로젝트 로컬 모드예요. `pangi init`은 이 경로를 `.gitignore`에 한 번만 추가해요.

```bash
# 프로젝트 안에 `.pangi` 실행 환경을 만들고 최초 관리자용 Bootstrap URL을 발급해요.
uv run pangi init --project-local --yes

# `.pangi/pangi.toml`의 형식과 설정값이 유효한지 확인해요.
uv run pangi config validate --project-local

# 현재 DB 버전과 적용 대기 중인 Migration을 확인해요. 실제로 적용하지는 않아요.
uv run pangi migrate plan --project-local

# 외부 연동 검사를 건너뛰고 로컬 실행 환경을 읽기 전용으로 진단해요.
uv run pangi doctor --project-local --offline

# 현재 터미널에서 Pangi 서버를 시작해요.
uv run pangi start --project-local
```

`pangi init`이 출력한 `http://127.0.0.1:8787/bootstrap#...` URL은 최초 Admin을 만들 때 한 번만 사용해요. URL을 잃었거나 만료됐다면 Admin을 만들기 전에만 아래 명령으로 기존 Grant를 취소하고 새 URL을 발급할 수 있어요.

```bash
uv run pangi bootstrap rotate --project-local --yes
```

Runtime을 시작한 뒤 Bootstrap URL에서 Admin을 만들고 `/login`에서 같은 계정으로 로그인하세요. 기본 주소는 `http://127.0.0.1:8787`이에요. 다른 Terminal에서는 다음 명령으로 상태를 확인하세요.

```bash
uv run pangi status --project-local --json
```

Session은 기본 12시간 동안 유지돼요. 생성 후 30분이 지나면 UI에서 Session을 명시적으로 회전할 수 있어요. Loopback이 아닌 Host에서는 HTTPS가 아니면 로그인을 거부해요.

## 테스트와 검증

### Backend 전체 검증

아래 명령은 Lint, 정적 타입, 전체 Test와 OpenAPI Drift를 확인해요.

```bash
uv run ruff check .
uv run mypy src
uv run pytest
uv run python scripts/export_openapi.py --check
```

### Model Routing과 Egress Policy만 검증

WBS-07.1~07.4.2에서 구현한 Egress Policy, OpenAI·Bedrock 요청 변환, 구조화 출력 검증, Transport Retry, Policy·Invocation 영속화, 관리자 조회·활성화 Gate API와 Dashboard를 확인하세요.

```bash
uv run pytest \
  tests/unit/test_model_routing.py \
  tests/unit/test_model_policy_management.py \
  tests/unit/test_model_provider_adapters.py \
  tests/contract/test_model_egress_contract.py \
  tests/contract/test_model_policy_web_contract.py \
  tests/contract/test_model_provider_retry_contract.py \
  tests/contract/test_openapi_contract.py \
  tests/integration/test_model_routing_persistence.py \
  tests/integration/test_sqlite_migrations.py \
  tests/architecture/test_dependency_rules.py \
  tests/smoke/test_cli.py

npm --prefix ui run check
npm --prefix ui run build
```

### Run 기능만 검증

WBS-05에서 구현한 Domain, Schema, Idempotency, Cursor, Owner Scope, Queue·복구와 Event API를 빠르게 확인하려면 다음 Test를 실행하세요.

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

### Root Orchestrator와 실행 Engine만 검증

WBS-08.1~08.5.3에서 구현한 Decision·Plan 검증, Root 단일 호출, Plan·Result 영속화, Dependency 실행, 결정적 Reducer, `SafeOutput` 완료 영속화, 보호된 Run API와 Queue·ASGI Runtime 조립을 확인하세요.

```bash
uv run pytest \
  tests/unit/test_orchestration_contracts.py \
  tests/unit/test_plan_validator.py \
  tests/unit/test_root_context.py \
  tests/unit/test_root_orchestrator.py \
  tests/unit/test_root_catalog.py \
  tests/unit/test_model_provider_router.py \
  tests/unit/test_orchestration_execution_contracts.py \
  tests/unit/test_orchestration_lifecycle.py \
  tests/unit/test_result_reducer.py \
  tests/unit/test_run_submissions.py \
  tests/unit/test_runtime_lifecycle.py \
  tests/contract/test_root_orchestration_model_contract.py \
  tests/contract/test_run_web_contract.py \
  tests/contract/test_openapi_contract.py \
  tests/integration/test_orchestration_execution.py \
  tests/integration/test_orchestration_lifecycle_persistence.py \
  tests/integration/test_run_submission_runtime.py \
  tests/integration/test_root_runtime_composition.py \
  tests/integration/test_web_runtime.py \
  tests/architecture/test_dependency_rules.py
```

### Input Guardrail만 검증

WBS-06.1에서 구현한 신뢰 계약, 입력 정규화, Principal·Attachment·Explicit Skill·Rate Limit 검사와 Guardrail 선행 Run 영속화 경계는 다음 명령으로 확인하세요.

```bash
uv run pytest \
  tests/unit/test_input_guardrails.py \
  tests/integration/test_guarded_run_submission.py \
  tests/architecture/test_dependency_rules.py
```

### 중앙 Redaction과 External Data만 검증

WBS-06.2에서 구현한 Versioned Redaction, External Text·HTML 정규화, 비신뢰 Envelope와 경계 Escape를 확인하세요.

```bash
uv run pytest \
  tests/unit/test_redaction_service.py \
  tests/unit/test_external_data_service.py \
  tests/unit/test_output.py \
  tests/architecture/test_dependency_rules.py
```

### Tool Guardrail만 검증

WBS-06.3에서 구현한 Stable Tool ID, Connection Owner, Permission·Schema, Approval, Call·Byte·Timeout Budget과 강제 실행 경계를 확인하세요.

```bash
uv run pytest \
  tests/unit/test_tool_guardrails.py \
  tests/architecture/test_dependency_rules.py
```

### 최종 Output Guardrail만 검증

WBS-06.4.1에서 구현한 Secret·Stack·내부 Path 제거, HTML Escape, Markdown·Evidence Link, Mention과 UTF-8 Byte 경계를 확인하세요.

```bash
uv run pytest \
  tests/unit/test_output_guardrails.py \
  tests/architecture/test_dependency_rules.py
```

### Log와 Run Event Redaction만 검증

WBS-06.4.2에서 구현한 정책 결정성, Log Argument·Extra·Exception 처리, SQLite 전체 Event 쓰기 경로와 JSON·SSE 비노출을 확인하세요.

```bash
uv run pytest \
  tests/unit/test_telemetry_redaction.py \
  tests/unit/test_logging_redaction.py \
  tests/integration/test_telemetry_delivery.py \
  tests/architecture/test_dependency_rules.py
```

### Append-only Audit만 검증

WBS-06.5에서 구현한 Audit 정규화·Redaction·Fingerprint, SQLite Append-only·Retention 제약, Bootstrap·Migration 기록과 Admin 조회 API를 확인하세요.

```bash
uv run pytest \
  tests/unit/test_audit_service.py \
  tests/integration/test_audit_persistence.py \
  tests/contract/test_audit_web_contract.py \
  tests/architecture/test_dependency_rules.py
```

### 보안 정책 영향과 신뢰 경계만 검증

WBS-06.6에서 구현한 정책 집합 Fingerprint·변경 감지와 외부 문서의 System·Tool Policy 비승격 계약을 확인하세요.

```bash
uv run pytest \
  tests/unit/test_policy_impact.py \
  tests/contract/test_guardrail_security_contract.py \
  tests/unit/test_external_data_service.py \
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

- `architecture`: Package 의존 방향과 Public API 경계를 검사해요.
- `smoke`: 설치 후 Import, CLI와 Package Resource를 검사해요.
- `contract`: CLI, Web 보안과 OpenAPI의 안정된 입출력 계약을 검사해요.
- `integration`: SQLite Transaction·Migration·인증·Runtime과 Run 영속성을 검사해요.
- `unit`: Config, Domain Policy와 Application Service를 외부 Runtime 없이 검사해요.

### Admin UI 검증

```bash
npm --prefix ui run check
npm --prefix ui run build
```

`check`는 OpenAPI 생성 Type과 TypeScript를 검사해요. `build`는 같은 검증을 수행한 뒤 배포용 정적 Asset을 `src/pangi/web/static`에 생성해요.
