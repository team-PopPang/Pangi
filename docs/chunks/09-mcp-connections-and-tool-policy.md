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
2. **Connection·Tool Policy 영속화(WBS-09.2)**: `connections`, `connection_tools`, `tool_policies`, `tool_invocations` Migration과 Repository를 구현하고 Stable Resolver·Policy·Schema·Budget Adapter를 WBS-06 Guardrail에 주입한다.
3. **SecretStore와 stdio Transport(WBS-09.3)**: Keyring 우선 SecretStore와 암호화 File Vault Fallback, stdio Command·Argument·Environment 정책과 Fake Server Fixture를 구현한다.
4. **Streamable HTTP와 OAuth Lifecycle(WBS-09.4)**: HTTPS·Redirect·DNS 정책, OAuth Discovery·PKCE·Callback와 Token Refresh·Revoke를 구현한다.
5. **Discovery·Guarded Tool 실행·Result 정규화(WBS-09.5)**: Tool/Resource/Prompt Discovery, Cache·변경 감지, MCP Executor, Timeout·Byte Limit, External Data Envelope와 Invocation Metric을 연결한다.
6. **Connection API와 Admin UI(WBS-09.6)**: Catalog, 연결 목록·Card, 연결·재연결·끊기·진단, Tool Policy 관리 API와 화면을 구현한다.

위 하위 번호는 현재 확인된 책임 경계다. 구현 중 독립적인 결과나 별도 보안·검증 Gate가 확인되면 루트 WBS 운영 규칙에 따라 단계를 더 나눈다.

## 기술 설계

- HTTP는 기본 HTTPS, stdio는 절대 경로/등록 Alias와 Argument Array만 허용한다.
- OAuth는 Authorization Code+PKCE S256, State/Nonce/Redirect/Resource Audience를 검증한다.
- SQLite에는 `secret_ref`만 저장하고 실제 값은 Keyring/Secret Manager/암호화 Vault에 둔다.
- WBS-03 Unit of Work 위에서 `connections`, `connection_tools`, `tool_policies`, `tool_invocations`의 Migration, 제약과 Repository를 이 WBS가 소유한다.
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
- [ ] Stable Tool Registry, 기본 Deny Policy와 Argument/Scope/Approval/Budget Adapter를 구현하고 WBS-06 공통 Enforcer에 조립한다.
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
- 실제 SQLite, MCP SDK, SecretStore, Transport, OAuth, API와 Admin UI는 WBS-09.2~09.6에 남겼다.

## 완료 조건

- MCP stdio와 Streamable HTTP 연결을 생성하고 Tool을 Discovery할 수 있다.
- User/Instance Scope가 교차되지 않고 새 Tool은 기본 Deny다.
- 연결 Lifecycle과 안전한 상태/시간/Qualifier/Mask를 UI에서 확인한다.
- Token과 Secret의 평문 노출이 0건이다.

## 미결정 사항

- MCP SDK v2의 최종 고정 Patch Version
- 조직 전용 Catalog Manifest 배포 방식
- Remote MCP Cache TTL의 운영 기본값
