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

## 내부 구현 단계

WBS 번호와 문서는 유지하고 아래 실행 단위를 독립 PR로 구현한다. 단계 수와 범위는 고정값이 아니다. 새로운 위험 경계나 별도 검증 Gate가 필요해 분해를 구체적으로 바꿔야 하면 영향 범위를 정리해 사용자 또는 제품 책임자의 승인을 받은 뒤 조정한다.

1. **신뢰 계약과 Input Guardrail**: 신뢰 수준·판정 계약, Principal·입력·첨부·Explicit Skill·Rate Limit 검증과 보호된 Run 제출 경계를 구현한다.
2. **중앙 Redaction과 External Data Envelope**: 비신뢰 외부 데이터의 출처·신뢰 Metadata와 Secret-safe 중앙 Redaction 경계를 구현한다.
3. **Tool Permission·Approval·Budget**: Stable Tool ID, Scope, Schema, 권한·승인과 호출·Byte·Timeout Budget을 검증한다.
4. **Output Guardrail과 Log·Event Redaction**: 최종 출력, Mention·Link·HTML·Stack Trace와 Log/Event 유출을 차단한다.
5. **Append-only Audit**: Audit 계약·저장소·관리자 조회 기반과 필수 관리 Action 기록을 구현한다.

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
- Input Guardrail은 활성 Principal과 요청 Principal의 사용자 ID·역할 일치를 먼저 검사한다.
- 본문은 CRLF/CR을 LF로 바꾸고 Unicode NFC로 정규화한다. 탭과 줄바꿈을 제외한 Control Character 및 주입된 Unicode Policy의 Bidi/Hidden Codepoint를 거부한 뒤 UTF-8 Byte Limit을 적용한다.
- Attachment는 개수, 필수 크기·MIME Metadata, 개별·전체 Byte Limit과 허용 MIME을 검사한다. MIME은 비교 전에 공백을 제거하고 소문자로 정규화한다.
- Explicit Skill 식별자는 이 단계에서 해석하거나 다시 쓰지 않고 주입된 접근 Port에 그대로 전달한다. 최종 Skill 이름·Version 형식은 WBS-11이 소유한다.
- 마지막으로 사용자·Channel Scope의 단일 Process Sliding Window Rate Limit을 예약한다. Limit, Window와 최대 추적 Key 수는 주입하며 조직 공통 기본값은 아직 고정하지 않는다.
- 모든 검사를 통과한 정규화 요청만 기존 `RunService.create_run`에 전달한다. 중복 Idempotency 판정과 Run·첫 Event·Idempotency Record의 원자 저장은 WBS-05의 기존 SQLite 경계가 계속 소유한다.
- 차단 판정과 예외는 안정적인 Error Code, 정책 Version·Fingerprint, 본문 Byte 수와 Attachment 개수 같은 안전한 Metadata만 포함한다. 요청 본문, Idempotency Key와 Attachment Reference를 표현하지 않는다.
- 자연어 URL을 임의로 다시 쓰거나 위험 의도를 Keyword로 분류하지 않는다.
- Tool Guardrail은 Stable Tool ID, Connection Owner, JSON Schema, Permission, Approval, Call/Byte/Timeout Budget을 순서대로 검사한다.
- Output Guardrail은 Secret Pattern, Token Prefix, HTML, Mention, 길이, Link Scheme, Stack Trace와 Evidence를 검사한다.
- 외부 Text는 Source/Trust Metadata를 가진 Envelope로 감싸며 내부 지시로 승격하지 않는다.
- Audit은 Append-only이며 원문 Token/Prompt/Tool Result 대신 Version, Fingerprint와 Redacted Diff를 저장한다.
- WBS-03 Unit of Work 위에서 `audit_events`의 Migration, Append-only 제약과 Repository를 이 WBS가 소유한다.
- Security Header, Session Rotation과 Localhost 기본 Bind는 Web Middleware에서 강제한다.

## 구현 체크리스트

- [x] 신뢰 수준, Guardrail Result와 Error Code 계약을 정의한다.
- [x] Input 정규화, 크기/MIME/Rate Rule과 기존 Idempotency 경계 앞의 보호된 Run 제출을 구현한다.
- [ ] Tool Policy와 Approval 검증 Engine의 공통 단계를 구현한다.
- [ ] Output Sanitizer, Mention/Link Policy와 Secret Redaction을 구현한다.
- [ ] External Data Envelope와 Control Character/HTML 정규화를 구현한다.
- [ ] JSON Log와 Event의 중앙 Redaction Filter를 구현한다.
- [ ] Audit Event Port, SQLite Adapter와 관리자 조회 API 기반을 만든다.
- [ ] CSP, Frame, Content-Type, Same-origin과 기본 Bind 정책을 적용한다.
- [ ] Guardrail/Audit 변경을 Eval 영향 대상으로 표시하는 Fingerprint를 만든다.

## 검증 체크리스트

- [x] 비활성 Principal, 대형 입력, 금지 MIME를 차단하고 허용된 중복 요청이 Run 하나로 Replay되는지 확인한다.
- [ ] Deny Tool, 다른 사용자 Connection과 승인 없는 Write 호출이 실행되지 않는지 확인한다.
- [ ] Secret, Stack Trace, 내부 Path와 Mention 폭주가 출력에서 제거되는지 확인한다.
- [ ] 외부 문서의 지시가 System/Tool Policy를 바꾸지 못하는지 Contract Test를 실행한다.
- [ ] 모든 필수 관리 Action이 Actor와 안전한 Diff를 Audit하는지 확인한다.
- [ ] Audit/Log/Event에 Secret 원문이 없는지 Fixture 기반으로 검사한다.

## 1차 구현 결과

- Framework 의존성이 없는 `TrustLevel`, Guardrail Stage·Outcome·Error Code와 정책·판정 계약을 추가했다.
- 모든 크기, MIME, Unicode와 Rate Limit 값을 `InputGuardrailPolicy`로 주입하고 Canonical SHA-256 정책 Fingerprint를 계산한다. Runtime Config나 조직 기본값은 추가하지 않았다.
- 비활성 Principal, 사용자 ID·역할 불일치, Control/Bidi/Hidden Unicode, UTF-8 Byte Limit, Attachment Metadata·개수·크기·MIME와 Explicit Skill 접근을 고정된 순서로 검사한다.
- CRLF/CR, NFC와 MIME을 정규화한다. ZWJ를 일괄 차단하지 않아 결합 Emoji를 보존하고, 금지 Hidden Codepoint는 Version이 붙은 정책 집합으로 주입한다.
- 사용자·Channel Scope를 원문 식별자 없이 Hash한 Key로 사용하는 bounded in-memory Sliding Window Rate Limiter를 추가했다.
- `GuardedRunSubmissionService`가 Guardrail을 통과한 요청만 기존 `RunService`에 넘긴다. 차단 요청은 Run 생성 Port를 호출하지 않으며 Run, Event와 Idempotency Record를 만들지 않는다.
- 허용된 정규화 요청의 SQLite Idempotency Replay, 차단 시 무영속화, 한국어·Emoji Byte 경계와 오류·결과 표현의 원문 비노출을 Unit·Integration Test로 고정했다.
- 아직 실제 Run 생성 HTTP/Channel 진입점에는 조립하지 않았다. WBS-08·11·16의 요청 수신 경계는 이 보호된 제출 서비스를 사용해야 한다.
- Tool·Output Guardrail, 중앙 Redaction·External Data Envelope와 Append-only Audit이 남아 있으므로 WBS-06 상태는 `진행 중`으로 유지한다.

## 완료 조건

- Guardrail 차단 요청은 Root/Provider/Tool을 호출하지 않는다.
- Unknown/Denied Tool과 교차 사용자 Token 실행이 0건이다.
- Secret Leak과 Chain-of-Thought 저장·노출이 0건이다.
- 필수 보안·관리 변경이 누락 없이 Append-only Audit에 기록된다.

## 미결정 사항

- 조직별 기본 Rate Limit Profile
- Secret Pattern Set의 업데이트와 오탐 승인 절차
- Destructive Tool의 2차 Admin Approval 적용 범위
