# WBS-09 MCP 연결과 Tool Policy

## 요약

stdio/Streamable HTTP MCP를 사용자·인스턴스 Scope로 연결하고, OAuth/Secret/Discovery/Policy를 거친 Tool만 실행하며 연결 상태와 진단을 Admin UI에서 관리한다.

## 목표

- MCP SDK Revision을 Adapter 내부에 격리한다.
- User Connection Token과 Instance Connection을 섞지 않는다.
- Tool Schema Fingerprint와 새 Tool 기본 Deny를 강제한다.
- Token/Secret을 DB, API, Log와 화면에 평문으로 노출하지 않는다.

## 선행 작업

- WBS-03
- WBS-04
- WBS-06

## 설계서 참조

- [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md): Section 8.3, 10, 14.3, 17.1, 21.3, 23.2~23.4, 24 Phase 2

## 범위

- MCP stdio/Streamable HTTP Client Adapter
- Connection Lifecycle, User/Instance Scope와 Health
- OAuth 2.1/PKCE/Resource Indicator와 SecretStore
- Tool/Resource/Prompt Discovery, Cache와 Fingerprint
- Tool Registry·Policy/Approval/Budget 영속 Adapter, Argument Schema Adapter, Result Normalization과 Metric
- Connection Catalog, Card, 연결/재연결/끊기/진단 UI

## 범위 밖

- Legacy SSE 신규 연결
- 서비스별 전용 SDK를 Core에 내장
- Web Search 전용 SSRF Pipeline
- Software Delivery의 Repository Write Worker

## 구현 순서

1. **Connection·Tool Registry 계약과 Lifecycle 상태기계(WBS-09.1)**: User/Instance Scope, Transport·Auth·상태 불변식, 허용 Lifecycle 전이와 제한된 Canonical Tool Schema Fingerprint를 구현하고 WBS-06 `ResolvedTool` 계약에 연결한다.
2. **Connection·Tool Registry SQLite 기반(WBS-09.2.1)**: `connections`, `connection_tools` Migration과 Repository를 구현하고 전역 Stable Tool Resolver를 WBS-06 Guardrail에 주입한다.
3. **Tool Policy·Schema Validator·Call Budget SQLite 기반(WBS-09.2.2.1)**: `tool_policies`, `tool_call_budgets` Migration과 불변 Policy Version·CAS 활성화, 안전한 JSON Schema Adapter와 원자적 Budget 예약을 구현한다.
4. **Approval Grant 발급·원자적 소비(WBS-09.2.2.2.1)**: `tool_approvals` Migration과 Hash Reference 발급, 만료·Claim·현재 권한 재검증과 일회성 소비 Adapter를 구현해 WBS-06 Guardrail에 주입한다.
5. **Tool Invocation Lifecycle(WBS-09.2.2.2.2)**: `tool_invocations` Migration과 실행 전후 상태·Metric 영속 Adapter를 구현하고 모든 Tool 실행이 이 기록 경계를 통과하게 한다.
6. **SecretStore와 stdio Transport(WBS-09.3)**: Keyring 우선 SecretStore와 암호화 File Vault Fallback, stdio Command·Argument·Environment 정책과 Fake Server Fixture를 구현한다.
7. **Streamable HTTP와 OAuth Lifecycle(WBS-09.4)**: HTTPS·Redirect·DNS 정책, OAuth Discovery·PKCE·Callback와 Token Refresh·Revoke를 구현한다.
8. **Discovery·Guarded Tool 실행·Result 정규화(WBS-09.5)**: Tool/Resource/Prompt Discovery, Cache·변경 감지, MCP Executor, Timeout·Byte Limit, External Data Envelope와 Invocation Metric을 연결한다.
9. **Connection API와 Admin UI(WBS-09.6)**: Catalog, 연결 목록·Card, 연결·재연결·끊기·진단, Tool Policy 관리 API와 화면을 구현한다.

위 하위 번호는 현재 확인된 책임 경계다. 구현 중 독립적인 결과나 별도 보안·검증 Gate가 확인되면 루트 WBS 운영 규칙에 따라 단계를 더 나눈다.

## 기술 설계

- HTTP는 기본 HTTPS, stdio는 절대 경로/등록 Alias와 Argument Array만 허용한다.
- OAuth는 Authorization Code+PKCE S256, State/Nonce/Redirect/Resource Audience를 검증한다.
- SQLite에는 `secret_ref`만 저장하고 실제 값은 Keyring/Secret Manager/암호화 Vault에 둔다.
- WBS-03 Unit of Work 위에서 WBS-09.2.1은 `connections`, `connection_tools`를, WBS-09.2.2.1은 `tool_policies`, `tool_call_budgets`를, WBS-09.2.2.2.1은 `tool_approvals`를, WBS-09.2.2.2.2는 `tool_invocations`의 Migration, 제약과 Repository를 소유한다.
- Discovery 결과는 Canonical JSON SHA-256 Fingerprint로 식별하고 변경 시 참조 Skill을 `needs_review`로 바꾼다.
- 새 Tool은 `deny`로 등록하고 Registry·Policy·Schema·Approval·Budget Adapter를 WBS-06의 공통 Tool Guardrail에 주입한다. 공통 Engine을 우회하지 않고 모든 검사를 통과한 `GuardedToolCall`만 MCP Client로 보낸다.
- Result는 Byte/Timeout Limit 뒤 표준 `ToolResult`와 비신뢰 Data Envelope로 정규화한다.
- Catalog는 연결 여부와 무관하게 필요한 서비스, Capability와 설치 안내를 보여준다.

## 구현 체크리스트

- [x] Connection/Tool/Policy Domain Model과 Lifecycle을 구현한다.
- [ ] stdio와 Streamable HTTP Adapter 및 Fake Server Fixture를 만든다.
- [ ] OAuth Discovery, PKCE, Callback와 Token Refresh/Revoke를 구현한다.
- [ ] Keyring 우선 SecretStore와 암호화 File Vault Fallback을 구현한다.
- [ ] Discovery Cache, Fingerprint, Refresh와 `list_changed` 처리를 구현한다.
- [x] Connection Registry, Tool Snapshot과 전역 Stable Tool Resolver를 SQLite에 영속화한다.
- [x] 불변 Tool Policy Version, 기본 Deny, 안전한 Argument Schema와 영속 Call Budget Adapter를 구현한다.
- [x] Approval Grant 발급·만료·원자적 일회성 소비 Adapter를 구현하고 WBS-06 공통 Enforcer에 조립한다.
- [x] Tool Invocation Lifecycle Adapter를 구현하고 모든 Tool 실행 전후 상태를 영속화한다.
- [ ] Result Normalizer, Redaction, Timeout/Byte Limit과 Invocation Metric을 구현한다.
- [ ] Connection/Tool API와 Catalog/Card/진단 UI를 구현한다.
- [ ] Schema Drift가 Skill에 미치는 영향 분석을 연결한다.

## 검증 체크리스트

- [ ] stdio/HTTP Fake MCP와 Tool Result Contract Test를 실행한다.
- [ ] 다른 사용자의 OAuth Token 선택이 불가능한지 확인한다.
- [ ] 새 Tool과 변경된 Schema가 기본 Deny/Review 상태인지 확인한다.
- [ ] PKCE, State, Resource Audience와 Scope 부족 경로를 테스트한다.
- [ ] Redirect/DNS/stdio Command/Environment 정책 위반을 차단한다.
- [ ] Token이 DB/API/Log/Card와 Backup에 평문으로 없는지 검사한다.
- [ ] 연결/재연결/끊기/진단과 Region/Workspace Qualifier를 E2E로 확인한다.

## 1차 구현 결과

- WBS-06의 `ToolConnectionScope`를 Connection Scope로 재사용하고 stdio/Streamable HTTP Transport, 인증 유형, Connection 상태와 Tool Registry 상태를 Framework-free Domain 계약으로 추가했다.
- User Scope Owner 필수·Instance Scope Owner 금지, Transport별 Command/Endpoint 상호 배타, 인증 유형과 Transport 조합, 시간·상태 Metadata 불변식을 고정했다.
- `disconnected`, `connecting`, `connected`, `degraded`, `error`의 허용 전이와 안전한 `connection_invalid_state_transition` 오류를 구현했다. 실제 연결 Side Effect와 Disconnect 정리 순서는 아직 실행하지 않는다.
- Tool Schema의 깊이·항목·UTF-8 Byte를 제한한 뒤 Object Key를 정렬한 Canonical JSON SHA-256 Fingerprint와 불변 `ToolRegistrySnapshot`을 생성한다.
- Registry Snapshot은 Connection Scope·Owner, Stable Tool ID, Remote Name, Permission과 Schema Fingerprint를 기존 WBS-06 `ResolvedTool`로 손실 없이 변환한다. `new`, `changed`, `unavailable` Tool은 비활성으로 해석하고 `active` Tool도 명시 Policy가 없으면 Guardrail이 기본 Deny한다.
- Endpoint, Command, Owner, Secret Reference, Remote Tool Name과 Schema 원문을 객체 표현과 오류에서 제외했다.
- 실제 SQLite Registry, MCP SDK, SecretStore, Transport, OAuth, API와 Admin UI는 WBS-09.2~09.6에 남겼다.

## 2차 구현 결과

- Migration 9에서 `connections`, `connection_tools`를 추가하고 User Scope Owner, Instance Scope, Transport·Auth, 상태·시간, Config 형태, Tool Permission·Schema·Fingerprint와 Discovery 시간 제약을 DB에서도 강제한다.
- Connection의 생성과 조회, Revision Compare-and-Swap 갱신을 구현했다. Scope·Owner·생성 시각은 갱신할 수 없고 새 Revision은 정확히 1씩 증가한다.
- HTTP Endpoint나 stdio Command·Argument는 형태가 고정된 Canonical `config_json`으로 저장한다. 인증 값은 저장하지 않고 불투명한 `secret_ref`만 보존한다.
- `stable_tool_id`를 전체 Registry에서 유일하게 고정하고 Tool Snapshot의 동일 시각 충돌과 오래된 Discovery 덮어쓰기를 거부한다.
- SQLite Registry를 WBS-06 `StableToolResolver`로 연결했다. Tool이 `active`이고 Connection이 `connected`일 때만 실행 가능한 대상으로 해석하며 Policy가 없으면 기존 Guardrail에서 실행 전에 차단한다.
- 2차 구현 시점에는 Tool Policy·Schema Validator·Call Budget, Approval·Invocation 저장소, 실제 MCP Transport·Discovery 실행과 SecretStore를 후속 범위로 남겨두었다.

## 3차 구현 결과

- WBS-09.2.2를 독립적인 영속·검증 경계에 따라 WBS-09.2.2.1과 WBS-09.2.2.2로 나눴다. 이번 단계는 Policy·Schema·Budget만 구현하고 Approval·Invocation은 다음 단계로 남겼다.
- Migration 10에서 `tool_policies`, `tool_call_budgets`를 추가했다. Policy Version은 초안으로만 생성하고 `draft → active → retired` 전이만 허용하며, Tool별 Active Version을 하나로 제한한다.
- Policy 활성화는 Candidate와 Baseline, 현재 Connection·Tool 상태, Permission과 Schema Fingerprint를 다시 확인한다. 기존 Active 폐기, Candidate 활성화와 `tool_policy.version_activated` Audit 기록을 하나의 Transaction에서 처리한다.
- 정확한 Active Policy가 없으면 기본 Deny한다. 오래된 Baseline, Registry 변경, 중복 Version과 불일치 Fingerprint는 외부 실행 전에 안전하게 거부한다.
- JSON Schema Adapter는 현재 Registry의 정확한 Schema Snapshot을 다시 확인하고 로컬 `$ref`만 허용한다. 제한된 Cache와 Worker Thread를 사용하며 Schema나 선택 의존성이 잘못되면 실패 폐쇄한다.
- Call Budget 예약은 Run·Stable Tool 단위로 SQLite에 영속화하고 Transaction 안에서 정확히 1씩 증가시킨다. 예약 직전에 Tool 가용성과 Active Policy Fingerprint를 다시 확인하고 Policy Version 변경으로 누적 횟수를 초기화하지 않는다.
- Policy 교체 경쟁, 병렬 Budget 예약, Process 재생성 뒤 누적 유지, 원격 `$ref` 거부, Fingerprint 불일치와 Secret 비노출을 Unit·Integration Test로 검증했다.
- 3차 구현 시점에는 Approval Grant 발급·소비·만료, Tool Invocation 상태·Metric, 실제 MCP 호출과 Result 정규화를 후속 범위로 남겼다.

## 4차 구현 결과

- WBS-09.2.2.2를 독립적인 상태 전이와 검증 Gate에 따라 Approval Grant와 Tool Invocation Lifecycle로 다시 나눴다. 이번 단계는 Approval만 구현하고 Invocation은 WBS-09.2.2.2.2로 남겼다.
- Migration 11에서 `tool_approvals`를 추가했다. Grant Claim은 불변이며 `active → consumed` 전이만 허용하고, 만료 전 정확히 한 번만 소비할 수 있다.
- 암호학적으로 안전한 Approval Reference는 발급 결과에서 한 번만 반환한다. SQLite에는 SHA-256 Hash만 저장하고 DB·Audit·오류·객체 표현에는 원문을 남기지 않는다.
- 발급 시 Subject·Approver 상태, Run Owner, Tool·Connection 상태, 활성 Policy Fingerprint와 Approval Requirement를 같은 Transaction에서 다시 검증한다. User Approval은 본인만, Admin Approval은 활성 Admin만 발급할 수 있다.
- 소비 시 Reference Hash와 Run·Tool·Argument·Policy Claim, 현재 사용자·Admin 권한과 활성 Policy를 다시 확인한다. Claim 불일치는 Grant를 소비하지 않고, 병렬 소비는 정확히 하나만 성공한다.
- 성공한 발급·소비와 `tool_approval.grant_issued`·`tool_approval.grant_consumed` Audit을 각각 하나의 Transaction으로 저장한다. Audit 실패 시 Grant 생성이나 소비도 함께 Rollback한다.
- WBS-06 Guardrail은 조회형 Approval Port 대신 원자적 소비 Port를 사용한다. 소비된 Grant ID를 `GuardedToolCall`에 고정하며 이후 Budget이 차단돼도 Approval을 환불하지 않는다.
- 역할·Run Owner·Policy 변경, 만료·중복·병렬 소비, Secret 비노출, DB 불변 제약과 Guardrail 실행 순서를 Unit·Integration Test로 검증했다.
- Tool Invocation 상태·Metric, Approval 요청 API·UI·Slack Action과 실제 MCP 실행은 후속 WBS 범위다.

## 5차 구현 결과

- Migration 12에서 `tool_invocations`를 추가하고 `denied`, `running`, `completed`, `failed`, `cancelled` 상태와 `running → terminal` 단방향 전이를 DB 제약과 Trigger로 고정했다.
- 실행 Context의 Run과 Tool 요청 Run을 Approval·Budget 전에 비교한다. 불일치는 `tool_run_context_mismatch`로 차단하고 Executor를 호출하지 않는다.
- Guardrail 거부는 `denied` Invocation과 `tool.invocation_denied` 내부 Run Event를 하나의 Transaction으로 저장한 뒤 기존 차단 오류를 다시 발생시킨다.
- 허용된 호출은 `running` Invocation과 `tool.invocation_started` Event를 먼저 Commit한다. 이후 SQLite Transaction을 닫고 Executor를 호출하므로 외부 I/O 중에는 DB Lock을 유지하지 않는다.
- Executor 성공·일반 실패·취소를 `completed`, `failed`, `cancelled`로 기록하고 `tool.invocation_finished` Event와 같은 Transaction에서 저장한다. 실행 시간은 Wall Clock이 아닌 주입 가능한 Monotonic Clock으로 측정한다.
- 일반 예외와 취소는 각각 `tool_execution_failed`, `tool_execution_cancelled`로 정규화한다. Argument·Result·Approval Reference·Secret과 예외 원문은 Invocation, Run Event와 공개 오류에 저장하지 않는다.
- `(run_id, stable_tool_id, calls_used)`를 유일한 Budget Attempt로 제한한다. 시작 기록 실패는 Executor 호출을 막고 종료 기록 실패는 외부 Tool 자동 재실행으로 이어지지 않으며 Approval과 Budget을 환불하지 않는다.
- 같은 Terminal 기록의 정확한 Replay는 Event를 중복 생성하지 않고 허용한다. 다른 Terminal 상태·시간·오류로 덮어쓰는 충돌은 거부한다.
- 상태 전이, 중복 Budget Attempt, Event 원자성, 실패·취소, Context 불일치, Secret 비노출과 v11→v12 Backup Migration을 Unit·Integration Test로 검증했다.
- 실제 MCP Transport·Discovery·Executor, Timeout·Result Byte 실행 강제와 `ToolResult` Summary·Fingerprint는 WBS-09.3~09.5에 남겼다. 비정상 종료 뒤 `running` Invocation 복구, 외부 Side Effect Exactly-once와 Retention도 이번 범위에 포함하지 않는다.

## 완료 조건

- MCP stdio와 Streamable HTTP 연결을 생성하고 Tool을 Discovery할 수 있다.
- User/Instance Scope가 교차되지 않고 새 Tool은 기본 Deny다.
- 연결 Lifecycle과 안전한 상태/시간/Qualifier/Mask를 UI에서 확인한다.
- Token과 Secret의 평문 노출이 0건이다.

## 미결정 사항

- MCP SDK v2의 최종 고정 Patch Version
- 조직 전용 Catalog Manifest 배포 방식
- Remote MCP Cache TTL의 운영 기본값
