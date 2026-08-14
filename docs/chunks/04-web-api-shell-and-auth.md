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
- HTTPS Session Cookie는 `__Host-` Prefix, HttpOnly/Secure/SameSite=Lax를 사용한다. 기본 Loopback HTTP 설치에서는 Host-only·HttpOnly·SameSite=Lax Cookie 예외를 사용하고 Loopback이 아닌 평문 HTTP 로그인을 거부한다.
- Session의 기본 절대 만료는 12시간이다. 생성·마지막 회전 후 30분이 지나면 명시적 회전을 권장하며 회전 시 Session·CSRF Token을 함께 바꾸고 이전 Token을 즉시 폐기한다.
- 상태 변경 API는 동일 출처, CSRF Cookie와 `X-CSRF-Token`을 함께 검증한다.
- Local Login은 Socket Peer IP 전체와 정규화 Local ID 조합 각각에 기본 5회/5분 실패 제한을 적용하며 신뢰 프록시 설정 전에는 Forwarded Header를 사용하지 않는다.
- Auth 우선순위는 Slack OIDC→신뢰된 Reverse Proxy Header→일회용 Local Bootstrap이다.
- 첫 Admin 생성 Transaction이 Bootstrap Token을 즉시 폐기한다.
- WBS-03 Unit of Work 위에서 `users`, `auth_identities`, `auth_sessions`, `bootstrap_grants`, `api_idempotency_records`의 Migration, 제약과 Repository를 이 WBS가 소유한다.
- User와 인증 수단은 분리한다. Local Identity만 Argon2id Password Hash를 가지며 Slack/Reverse Proxy Subject는 같은 `auth_identities` 유일성 경계를 사용한다.
- Bootstrap Grant 기본 TTL은 30분이고 활성 Grant는 최대 하나다. URL은 `/bootstrap#<token>`을 사용하고 DB에는 SHA-256 Hash만 저장한다.
- 최초 Admin과 Local Identity 생성, Grant 소비는 같은 Transaction이다. `init`는 Secret을 한 번만 발급하며 복구는 Admin 생성 전 `bootstrap rotate --yes`로만 수행한다.
- Member, Skill Author, Admin, System 역할을 API Dependency에서 검사하고 Resource Owner 조건은 Use Case가 재검사한다.
- 모든 오류는 Stable Code, 안전한 Message, Request ID와 제한된 Details를 가진 Envelope로 변환한다.
- React Router/TanStack Query/OpenAPI 생성 Type을 기본으로 하고 CI에서 Schema Drift를 검사한다.
- WBS-03의 SQLite Runtime 시작 상태와 Doctor 결과를 `/health/ready`에 연결하되 DB 구현 타입을 API 계약에 노출하지 않는다.

## 내부 구현 단계

WBS 번호와 문서는 늘리지 않고 아래 실행 단위를 여러 PR로 구현한다.

1. **ASGI Runtime과 빈 Admin Shell**: FastAPI/Uvicorn, SQLite Lifespan, Live/Ready, CLI `start/status`, 최소 React/Vite Shell과 정적 Asset 경계를 만든다.
2. **인증 영속성과 Bootstrap Admin**: User, Session, Bootstrap Grant Migration/Repository와 일회성 Local Admin 생성을 만든다.
3. **공통 API 보안 계약**: Request ID, Error Envelope, 역할, CSRF, Idempotency, Cursor Pagination과 OIDC Adapter Protocol을 만든다.
4. **Admin UI 계약과 SSE**: 전체 Sidebar/Layout, OpenAPI 생성 Type, API Client, SSE Transport와 Schema Drift Gate를 완성한다.

## 구현 체크리스트

- [x] ASGI App Factory와 Composition Root를 만든다.
- [x] Request ID와 Bootstrap API용 최소 Error Envelope를 구현한다.
- [x] 전체 오류 변환, Local 인증과 CSRF 경계를 구현한다.
- [ ] 실제 기능 Mutation과 연결된 Idempotency Middleware를 구현한다.
- [x] Session Store와 Role 기반 API Dependency를 구현한다.
- [x] 인증 Core Migration과 Local Bootstrap Admin 생성·Token 폐기를 구현한다.
- [ ] Slack/Reverse Proxy OIDC Adapter Protocol을 정의한다.
- [x] 최소 React/Vite SPA Shell과 Visual Token 기반을 구현한다.
- [ ] 전체 Sidebar와 공통 Layout을 구현한다.
- [ ] OpenAPI 생성 Type과 Frontend API Client를 연결한다.
- [x] `/health/live`와 `/health/ready`를 만든다.
- [ ] SSE 공통 Transport를 만든다.
- [x] Fingerprinted Asset과 SPA Fallback Cache 정책을 설정한다.

## 검증 체크리스트

- [x] Bootstrap Token 재사용, 회전된 Token과 만료를 동일한 안전 오류로 거부하는지 확인한다.
- [x] 역할별 API Dependency의 허용/거부를 테스트한다.
- [ ] 실제 기능 Use Case에서 Resource Owner 검사를 테스트한다.
- [x] CSRF 또는 동일 출처 근거가 없는 상태 변경 요청을 거부하는지 확인한다.
- [ ] 같은 Idempotency Key 요청이 중복 Mutation을 만들지 않는지 확인한다.
- [x] Bootstrap Error Envelope에 Stack Trace, 내부 Path와 Secret이 없는지 검사한다.
- [ ] OpenAPI와 Frontend Type Drift Test를 실행한다.
- [x] SPA Route, Asset Cache와 Live/Ready를 E2E로 확인한다.
- [ ] SSE 연결을 E2E로 확인한다.

## 1차 구현 결과

- FastAPI ASGI App Factory가 WBS-03의 SQLite Database를 Lifespan으로 시작하고 종료·실패 시 Connection과 Process Lock을 정리한다.
- Doctor의 실행 전 Port 검사와 분리된 Readiness 계약을 만들고 SQLite Runtime과 Package Asset 상태를 `/health/ready`에 연결했다.
- `/health/live`는 Pangi 제품 식별자와 Schema Version을 제공하고 `pangi status`가 이를 검증해 다른 Process의 열린 Port를 오인하지 않는다.
- Uvicorn 단일 Process 전경 실행을 `pangi start`에 연결하고 Migration 실패나 두 번째 SQLite Process가 Ready 이전에 실패하도록 유지했다.
- React/Vite 기반 빈 Admin Shell, Fingerprinted Asset, Non-API SPA Fallback과 API/Asset 404 경계를 Wheel Package Data에 포함했다.
- Same-origin 기본값을 유지하고 CSP, Frame 차단, `nosniff`, Referrer Policy와 Asset Cache Header를 적용했다.
- 실제 Process의 `start/status/종료`, ASGI Lifespan, Live/Ready, SPA/Asset와 설치 자원 계약을 검증했다.
- Bootstrap 이후의 실제 로그인·Session, 공통 API 보안 계약, 전체 Admin Layout, OpenAPI Type과 SSE가 남아 있으므로 WBS-04 상태는 `진행 중`으로 유지한다.

## 2차 구현 결과

- v2 Migration에 `users`, `auth_identities`, `auth_sessions`, `bootstrap_grants`를 추가하고 Role/Status, Provider Subject 유일성, Local Argon2id Hash, Foreign Key와 활성 Grant 단일성 제약을 DB에서 강제했다.
- Bootstrap Service가 256-bit Random Token을 URL Fragment로 한 번만 반환하고 DB에는 SHA-256 Hash와 기본 30분 만료만 저장한다.
- `pangi init`의 최초 발급과 멱등 재실행, `pangi bootstrap rotate --yes`의 명시적 복구를 연결하고 Admin 생성 뒤 Bootstrap을 닫았다.
- `/api/v1/bootstrap/admin`이 Grant 검증, Local Admin/Identity 생성과 Grant 소비를 WBS-03 Unit of Work 하나에서 처리한다. 실패 시 전체 변경을 Rollback한다.
- `/bootstrap` UI가 Fragment Token을 메모리로 옮긴 뒤 주소에서 즉시 제거하고 성공 후 Token과 Password 상태를 폐기한다.
- Bootstrap API에 Request ID, Same-origin 검사와 Secret을 포함하지 않는 최소 Error Envelope를 적용했다.
- Migration v1→v2, DB 제약, Grant 발급·회전·만료·재사용, Transaction Rollback, API 오류와 원문 Secret 비저장을 자동 테스트로 고정했다.
- 실제 Login, Session Cookie, CSRF, Role Dependency와 외부 OIDC가 남아 있으므로 WBS-04 상태는 `진행 중`이다.

## 3차 구현 결과

- Local Identity의 Argon2id Password를 검증하고 존재하지 않는 사용자도 Dummy Hash를 검증해 응답 차이를 줄였다. 잘못된 사용자·비밀번호·비활성 사용자는 같은 `invalid_credentials` 오류를 반환한다.
- 단일 Process 기준 로그인 실패를 Socket Peer IP 전체와 정규화 Local ID 조합 각각 기본 5회/5분으로 제한하고 제한 상태에서는 비싼 Password 검증 전에 거부한다.
- 기존 `auth_sessions`에 Session·CSRF Token의 SHA-256 Hash만 저장하고 생성, 기본 12시간 만료, 명시적 회전과 폐기를 WBS-03 Unit of Work로 직렬화했다.
- `POST /api/v1/auth/login`, `GET /api/v1/auth/session`, `POST /api/v1/auth/session/rotate`, `POST /api/v1/auth/logout`을 추가했다. 회전은 절대 만료를 연장하지 않고 두 Token을 함께 교체하며 이전 Token을 즉시 무효화한다.
- HTTPS에서는 `__Host-` Secure Cookie를 사용한다. 기본 `127.0.0.1` HTTP에서는 Host-only Cookie 예외와 CSRF·동일 출처 검증을 유지하고 Loopback이 아닌 평문 HTTP 로그인을 거부한다.
- API 404·405·422, 인증·권한 오류와 예기치 않은 500을 Request ID가 있는 공통 Error Envelope로 변환하고 예상하지 못한 오류는 Secret 없이 Request ID로 기록한다.
- `member`, `skill_author`, `admin`, `system`을 검사하는 FastAPI Dependency 기반을 추가하고 비활성 사용자는 기존 Session도 사용할 수 없게 했다.
- `/login` UI, 인증된 Shell Gate, 회전 권장 Session의 단일 회전 요청과 Logout을 연결했다. Bootstrap 성공 화면은 실제 Login 경로를 안내한다.
- Session 생성·만료·회전·폐기, 비활성 사용자, 로그인 제한, Cookie 속성, CSRF·Origin, 역할 Dependency와 Error Envelope를 자동 테스트로 고정했다.
- 실제 기능 API와 연결할 Idempotency·Cursor, Resource Owner 검사, Slack/Reverse Proxy OIDC와 전체 Admin Layout은 후속 범위이므로 WBS-04 상태는 `진행 중`이다.

## 완료 조건

- `pangi start`가 빈 Admin Shell과 Live/Ready Endpoint를 제공한다.
- 첫 Admin 생성 뒤 Bootstrap URL을 다시 사용할 수 없다.
- 인증되지 않았거나 역할이 부족한 Mutation은 실행되지 않는다.
- Frontend와 Backend가 같은 OpenAPI 계약을 사용한다.

## 미결정 사항

- Reverse Proxy OIDC Header의 최초 지원 목록
- Admin Shell의 Browser 지원 범위
