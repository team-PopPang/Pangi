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
- Tool Policy, Argument 검증, Result Normalization과 Metric
- Connection Catalog, Card, 연결/재연결/끊기/진단 UI

## 범위 밖

- Legacy SSE 신규 연결
- 서비스별 전용 SDK를 Core에 내장
- Web Search 전용 SSRF Pipeline
- Software Delivery의 Repository Write Worker

## 기술 설계

- HTTP는 기본 HTTPS, stdio는 절대 경로/등록 Alias와 Argument Array만 허용한다.
- OAuth는 Authorization Code+PKCE S256, State/Nonce/Redirect/Resource Audience를 검증한다.
- SQLite에는 `secret_ref`만 저장하고 실제 값은 Keyring/Secret Manager/암호화 Vault에 둔다.
- Discovery 결과는 Canonical JSON SHA-256 Fingerprint로 식별하고 변경 시 참조 Skill을 `needs_review`로 바꾼다.
- 새 Tool은 `deny`로 등록하고 Principal/Owner/Permission/Approval/Schema/Budget을 통과한 호출만 MCP Client로 보낸다.
- Result는 Byte/Timeout Limit 뒤 표준 `ToolResult`와 비신뢰 Data Envelope로 정규화한다.
- Catalog는 연결 여부와 무관하게 필요한 서비스, Capability와 설치 안내를 보여준다.

## 구현 체크리스트

- [ ] Connection/Tool/Policy Domain Model과 Lifecycle을 구현한다.
- [ ] stdio와 Streamable HTTP Adapter 및 Fake Server Fixture를 만든다.
- [ ] OAuth Discovery, PKCE, Callback와 Token Refresh/Revoke를 구현한다.
- [ ] Keyring 우선 SecretStore와 암호화 File Vault Fallback을 구현한다.
- [ ] Discovery Cache, Fingerprint, Refresh와 `list_changed` 처리를 구현한다.
- [ ] Stable Tool Registry, 기본 Deny와 Argument/Scope/Approval Enforcer를 구현한다.
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

## 완료 조건

- MCP stdio와 Streamable HTTP 연결을 생성하고 Tool을 Discovery할 수 있다.
- User/Instance Scope가 교차되지 않고 새 Tool은 기본 Deny다.
- 연결 Lifecycle과 안전한 상태/시간/Qualifier/Mask를 UI에서 확인한다.
- Token과 Secret의 평문 노출이 0건이다.

## 미결정 사항

- MCP SDK v2의 최종 고정 Patch Version
- 조직 전용 Catalog Manifest 배포 방식
- Remote MCP Cache TTL의 운영 기본값
