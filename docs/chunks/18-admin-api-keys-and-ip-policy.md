# WBS-18 Admin API Key와 IP Policy

## 요약

Admin의 외부 API 접근을 Scope/만료가 있는 API Key와 표면별 CIDR Allowlist로 통제하고, Trusted Proxy·Lockout 방지·재인증·사용 집계를 관리 화면과 Audit에 연결한다.

## 목표

- API Key 원문은 생성 응답에서 한 번만 표시한다.
- Key Scope/만료/폐기와 IP Policy를 모든 API 인증 전에 검사한다.
- Forwarded IP는 신뢰된 Proxy에서만 수용한다.
- Reverse Proxy OIDC Header는 신뢰된 Proxy에서만 인증 근거로 수용한다.
- Allowlist 변경 전 현재 Admin/OAuth/API Client 영향을 Preview해 Lockout을 막는다.

## 선행 작업

- WBS-03
- WBS-04
- WBS-06
- WBS-17

## 설계서 참조

- [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md): Section 16.1, 16.6, 17.5, 21.5~21.6, 22.1, 23.1~23.5, 24 Phase 7

## 범위

- 256-bit API Key 생성, Hash/Prefix, Scope, 만료와 폐기
- Key별 일별 Endpoint Group 사용 집계
- Dashboard/API/Both CIDR Allowlist와 IPv4/IPv6 정규화
- Trusted Proxy 해석, Access Decision과 제한된 Event
- Reverse Proxy OIDC Header Adapter와 기존 Identity·Session 연결
- 영향 Dry-run/Fingerprint, 재인증과 Local Recovery
- API Key/IP 승인/사용 기록/Admin Audit UI와 API

## 범위 밖

- OAuth Provider의 사용자 Token 수명주기
- 외부 WAF/Reverse Proxy 제품 설치
- 원문 IP의 무제한 보존
- 폐기된 API Key 복구

## 기술 설계

- Key는 `pangi_<environment>_<random>` 형식이며 생성 시 원문을 한 번 반환하고 Argon2/안전한 KDF Hash와 Prefix만 저장한다.
- 인증 Pipeline은 Hash Match→State→Expiry→Scope→IP Policy 순으로 검사하고 성공 시에만 `last_used_at`을 갱신한다.
- 사용 기록은 Key ID/Date/Endpoint Group/성공·실패/마지막 사용으로 집계하고 Raw 요청·응답/Header를 저장하지 않는다.
- WBS-03 Unit of Work 위에서 `api_keys`, `api_key_usage_daily`, `ip_allowlist_entries`, `ip_access_events`의 Migration, 보안 제약과 Repository를 이 WBS가 소유한다.
- CIDR은 표준 Network Address로 정규화하고 Host Bit 교정 Preview를 제공한다.
- `X-Forwarded-For`는 Socket Peer가 `trusted_proxy_cidrs`에 포함될 때만 해석한다.
- Reverse Proxy OIDC Header도 같은 Trusted Proxy 판정을 통과한 요청에서만 읽고 검증된 Subject를 `auth_identities(provider='reverse_proxy')`와 연결한다.
- Policy 변경은 현재 Client/OAuth Callback/Health/API Consumer 영향과 Fingerprint를 계산하고 재인증 뒤 적용한다.
- Local Recovery는 실행 Host 관리자만 수행하고 모든 변경을 Audit한다.

## 구현 체크리스트

- [ ] API Key 생성/Hash/Prefix/Scope/Expiry/State Domain을 구현한다.
- [ ] 원문 1회 응답과 Metadata-only 조회/Backup을 구현한다.
- [ ] API Key 인증 Middleware와 즉시 폐기를 구현한다.
- [ ] Endpoint Group 일별 사용 집계와 권한 조회를 구현한다.
- [ ] CIDR 정규화와 Surface별 Allowlist Engine을 구현한다.
- [ ] Trusted Proxy와 Client IP 해석을 구현한다.
- [ ] Reverse Proxy OIDC Header Adapter와 기존 User·Session 연결을 구현한다.
- [ ] Impact Preview/Fingerprint, 재인증과 Lockout 방지를 구현한다.
- [ ] Local Recovery CLI와 접근 허용/차단 Event를 구현한다.
- [ ] API Key, 사용 기록, IP 승인과 Audit 관리 화면/API를 구현한다.

## 검증 체크리스트

- [ ] API Key 원문이 생성 후 List/Get/DB/Log/Backup에서 재노출되지 않는지 검사한다.
- [ ] Scope/만료/폐기와 Key Rotation 경로를 테스트한다.
- [ ] 성공/실패 사용 집계와 `last_used_at` 갱신 조건을 확인한다.
- [ ] IPv4/IPv6/Host Bit/CIDR 중복과 Surface Matrix를 테스트한다.
- [ ] 신뢰되지 않은 Proxy의 Forwarded IP를 무시하는지 확인한다.
- [ ] 신뢰되지 않은 Peer의 OIDC Header를 무시하고 Header Spoofing을 거부하는지 확인한다.
- [ ] 현재 Admin을 차단하는 Policy 적용을 거부하거나 Recovery를 요구하는지 확인한다.
- [ ] 오래된 Impact Fingerprint와 우회 Callback/Health 규칙을 거부한다.

## 완료 조건

- API Key 원문은 생성 시에만 보이고 Scope·만료·마지막 사용·집계를 제공한다.
- 폐기한 Key는 즉시 모든 API에서 차단된다.
- CIDR/표면/Trusted Proxy 정책과 영향 Preview가 일관되게 적용된다.
- Reverse Proxy OIDC는 신뢰된 Proxy와 명시적으로 설정한 Header에서만 Principal을 만든다.
- Admin Lockout과 신뢰되지 않은 Forwarded IP 수용이 0건이다.

## 미결정 사항

- API Key Hash Algorithm의 최종 Parameter
- 원문 IP 선택 보존 Profile의 제공 여부
- OAuth Callback/Health의 기본 Allowlist 예외 정책
- Reverse Proxy OIDC의 최초 지원 Header 목록
