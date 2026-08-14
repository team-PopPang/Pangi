# WBS-06 Guardrail·보안·Audit

## 요약

사용자 입력, 모델 제안, Tool 호출과 최종 출력이 신뢰 경계를 넘을 때 서버 코드가 인증·권한·Schema·Redaction을 강제하고 중요한 변경을 Audit한다.

## 목표

- Input, Tool, Output Guardrail을 모델과 독립된 결정적 정책으로 구현한다.
- 외부 데이터와 모델 출력이 권한을 확대하지 못하게 한다.
- Secret과 내부 경로가 Context, Event, Log와 응답에 유출되지 않게 한다.
- 보안·관리 변경의 Actor, Resource와 안전한 전후 Summary를 Append-only Audit에 남긴다.

## 선행 작업

- WBS-02
- WBS-03
- WBS-05

## 설계서 참조

- [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md): Section 4.2, 5.2, 8.3, 21, 22.2, 23.1, 26

## 범위

- 신뢰 수준과 데이터 Envelope
- Input/Tool/Output Guardrail Pipeline
- Tool Permission/Approval/Call Budget의 공통 Policy 계약
- Secret Scanner와 Redaction Filter
- Append-only Audit Event와 관리자 조회 기반
- Security Header와 기본 Local Bind 정책

## 범위 밖

- Model Provider별 Egress 결정
- MCP Transport와 OAuth 상세
- API Key/CIDR 관리 화면
- Red Team Case 생성과 실행

## 기술 설계

- 사용자 요청, Dashboard 입력, 모델 출력과 MCP/Web Result는 비신뢰로 시작한다.
- Input Guardrail은 Identity, 크기, MIME, URL/Unicode 정규화, Explicit Skill, Rate Limit과 Idempotency를 검사한다.
- Tool Guardrail은 Stable Tool ID, Connection Owner, JSON Schema, Permission, Approval, Call/Byte/Timeout Budget을 순서대로 검사한다.
- Output Guardrail은 Secret Pattern, Token Prefix, HTML, Mention, 길이, Link Scheme, Stack Trace와 Evidence를 검사한다.
- 외부 Text는 Source/Trust Metadata를 가진 Envelope로 감싸며 내부 지시로 승격하지 않는다.
- Audit은 Append-only이며 원문 Token/Prompt/Tool Result 대신 Version, Fingerprint와 Redacted Diff를 저장한다.
- WBS-03 Unit of Work 위에서 `audit_events`의 Migration, Append-only 제약과 Repository를 이 WBS가 소유한다.
- Security Header, Session Rotation과 Localhost 기본 Bind는 Web Middleware에서 강제한다.

## 구현 체크리스트

- [ ] 신뢰 수준, Guardrail Result와 Error Code 계약을 정의한다.
- [ ] Input 정규화, 크기/MIME/Rate/Idempotency Rule을 구현한다.
- [ ] Tool Policy와 Approval 검증 Engine의 공통 단계를 구현한다.
- [ ] Output Sanitizer, Mention/Link Policy와 Secret Redaction을 구현한다.
- [ ] External Data Envelope와 Control Character/HTML 정규화를 구현한다.
- [ ] JSON Log와 Event의 중앙 Redaction Filter를 구현한다.
- [ ] Audit Event Port, SQLite Adapter와 관리자 조회 API 기반을 만든다.
- [ ] CSP, Frame, Content-Type, Same-origin과 기본 Bind 정책을 적용한다.
- [ ] Guardrail/Audit 변경을 Eval 영향 대상으로 표시하는 Fingerprint를 만든다.

## 검증 체크리스트

- [ ] 비활성 Principal, 대형 입력, 금지 MIME와 중복 요청을 차단한다.
- [ ] Deny Tool, 다른 사용자 Connection과 승인 없는 Write 호출이 실행되지 않는지 확인한다.
- [ ] Secret, Stack Trace, 내부 Path와 Mention 폭주가 출력에서 제거되는지 확인한다.
- [ ] 외부 문서의 지시가 System/Tool Policy를 바꾸지 못하는지 Contract Test를 실행한다.
- [ ] 모든 필수 관리 Action이 Actor와 안전한 Diff를 Audit하는지 확인한다.
- [ ] Audit/Log/Event에 Secret 원문이 없는지 Fixture 기반으로 검사한다.

## 완료 조건

- Guardrail 차단 요청은 Root/Provider/Tool을 호출하지 않는다.
- Unknown/Denied Tool과 교차 사용자 Token 실행이 0건이다.
- Secret Leak과 Chain-of-Thought 저장·노출이 0건이다.
- 필수 보안·관리 변경이 누락 없이 Append-only Audit에 기록된다.

## 미결정 사항

- 조직별 기본 Rate Limit Profile
- Secret Pattern Set의 업데이트와 오탐 승인 절차
- Destructive Tool의 2차 Admin Approval 적용 범위
