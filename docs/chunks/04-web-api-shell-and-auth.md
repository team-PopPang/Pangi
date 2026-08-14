# WBS-04 Web/API Shell과 인증

## 요약

FastAPI와 React Admin Shell을 같은 Origin으로 제공하고, Bootstrap Admin부터 역할 기반 API 접근까지 모든 후속 관리 기능이 공유할 Web 기반을 만든다.

## 목표

- `/`, `/assets/*`, `/api/v1/*`, Health와 SSE의 공통 Server 구조를 만든다.
- Slack OIDC, Reverse Proxy OIDC와 Local Bootstrap 인증 경계를 정의한다.
- 상태 변경 API의 인증, CSRF, Idempotency와 Error Envelope를 공통화한다.
- Frontend Type이 OpenAPI와 Drift하지 않도록 한다.

## 선행 작업

- WBS-01
- WBS-02
- WBS-03

## 설계서 참조

- [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md): Section 5, 16.1~16.6, 17, 18.5, 21.5, 24 Phase 0

## 범위

- FastAPI Composition과 공통 Middleware
- SPA Route/Asset Fallback과 Admin Sidebar Shell
- Bootstrap Admin, Session, Role과 CSRF
- Cursor Pagination, `Idempotency-Key`, Error Envelope
- OpenAPI 생성 Type과 Frontend API Client 기반
- Live/Ready Endpoint와 SSE Transport Shell

## 범위 밖

- Connection, Skill, Schedule 등 기능별 API 본문
- 최종 Analytics와 관리 페이지 데이터
- Slack Event Adapter
- Remote TLS Proxy 자체의 설치

## 기술 설계

- API와 Dashboard는 같은 Origin을 기본으로 하고 CORS는 비활성화한다.
- Session Cookie는 HttpOnly/Secure/SameSite=Lax를 사용하고 상태 변경 API는 CSRF Token을 요구한다.
- Auth 우선순위는 Slack OIDC→신뢰된 Reverse Proxy Header→일회용 Local Bootstrap이다.
- 첫 Admin 생성 Transaction이 Bootstrap Token을 즉시 폐기한다.
- WBS-03 Unit of Work 위에서 `users`, `auth_sessions`, `bootstrap_grants`, `api_idempotency_records`의 Migration, 제약과 Repository를 이 WBS가 소유한다.
- Member, Skill Author, Admin, System 역할을 API Dependency에서 검사하고 Resource Owner 조건은 Use Case가 재검사한다.
- 모든 오류는 Stable Code, 안전한 Message, Request ID와 제한된 Details를 가진 Envelope로 변환한다.
- React Router/TanStack Query/OpenAPI 생성 Type을 기본으로 하고 CI에서 Schema Drift를 검사한다.
- WBS-03의 SQLite Runtime 시작 상태와 Doctor 결과를 `/health/ready`에 연결하되 DB 구현 타입을 API 계약에 노출하지 않는다.

## 구현 체크리스트

- [ ] ASGI App Factory와 Composition Root를 만든다.
- [ ] Request ID, 오류 변환, 인증, CSRF와 Idempotency Middleware를 구현한다.
- [ ] Session Store와 Role 기반 API Dependency를 구현한다.
- [ ] Local Bootstrap Admin 생성과 Token 폐기를 구현한다.
- [ ] Slack/Reverse Proxy OIDC Adapter Protocol을 정의한다.
- [ ] SPA Shell, Sidebar, 공통 Layout와 Visual Token을 구현한다.
- [ ] OpenAPI 생성 Type과 Frontend API Client를 연결한다.
- [ ] `/health/live`, `/health/ready`와 SSE 공통 Transport를 만든다.
- [ ] Fingerprinted Asset과 SPA Fallback Cache 정책을 설정한다.

## 검증 체크리스트

- [ ] Bootstrap Token 재사용과 만료를 거부하는지 확인한다.
- [ ] 역할별 API 허용/거부와 Resource Owner 검사를 테스트한다.
- [ ] CSRF 없는 상태 변경 요청을 거부하는지 확인한다.
- [ ] 같은 Idempotency Key 요청이 중복 Mutation을 만들지 않는지 확인한다.
- [ ] Error Envelope에 Stack Trace, 내부 Path와 Secret이 없는지 검사한다.
- [ ] OpenAPI와 Frontend Type Drift Test를 실행한다.
- [ ] SPA Route, Asset Cache, Live/Ready와 SSE 연결을 E2E로 확인한다.

## 완료 조건

- `pangi start`가 빈 Admin Shell과 Live/Ready Endpoint를 제공한다.
- 첫 Admin 생성 뒤 Bootstrap URL을 다시 사용할 수 없다.
- 인증되지 않았거나 역할이 부족한 Mutation은 실행되지 않는다.
- Frontend와 Backend가 같은 OpenAPI 계약을 사용한다.

## 미결정 사항

- 기본 Session 만료 시간과 Rotation 주기
- Reverse Proxy OIDC Header의 최초 지원 목록
- Admin Shell의 Browser 지원 범위
