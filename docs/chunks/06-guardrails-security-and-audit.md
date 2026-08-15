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

1. **신뢰 계약과 Input Guardrail (WBS-06.1)**: 신뢰 수준·판정 계약, Principal·입력·첨부·Explicit Skill·Rate Limit 검증과 보호된 Run 제출 경계를 구현한다.
2. **중앙 Redaction과 External Data Envelope (WBS-06.2)**: 비신뢰 외부 데이터의 출처·신뢰 Metadata와 Secret-safe 중앙 Redaction 경계를 구현한다.
3. **Tool Permission·Approval·Budget (WBS-06.3)**: Stable Tool ID, Scope, Schema, 권한·승인과 호출·Byte·Timeout Budget을 검증한다.
4. **Output Guardrail (WBS-06.4.1)**: 최종 Markdown과 Evidence의 Secret·내부 정보·HTML·Link·Mention·길이를 공통 경계에서 정규화한다.
5. **Log·Run Event Redaction (WBS-06.4.2)**: 구조화 Log와 Run Event가 영속화·출력되기 전에 중앙 Redaction을 강제한다.
6. **Append-only Audit (WBS-06.5)**: Audit 계약·저장소·관리자 조회 기반과 필수 관리 Action 기록을 구현한다.

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
- 중앙 Redaction은 Versioned Rule Set을 사용해 Text와 중첩 JSON 호환 데이터에 같은 정책을 적용한다. 결과 Metadata에는 정책 Version·Fingerprint, Redaction Count와 Rule ID만 포함한다.
- Built-in `core-secret-v1`은 CLI가 사용하던 Credential 할당, 알려진 Token Prefix, 민감 Key와 `secret://` Reference 규칙을 소유한다. 조직별 Pattern, PII Redaction과 오탐 승인 절차는 포함하지 않는다.
- External Data는 `text/plain` 또는 `text/html`을 Byte Limit 안에서 정규화한다. 실행·비가시 HTML, Control/Bidi/Hidden Unicode를 제거한 뒤 중앙 Redaction을 적용하고 `TrustLevel.UNTRUSTED`로 고정한다.
- External Content Fingerprint는 원문이 아니라 정규화·Redaction 완료 Text로 계산한다. Source URL과 외부 식별자는 이 단계의 Metadata에 포함하지 않는다.
- Prompt용 `<external_data>` Renderer는 Source Attribute와 Content를 Escape한다. Envelope 안의 문장은 Evidence인 비신뢰 데이터이며 호출자가 신뢰 수준을 승격할 수 없다.
- Tool Guardrail은 활성 Principal과 Run 요청자의 사용자 ID, Stable Tool ID를 확인한 뒤 Connection Owner, 명시 Policy·Permission·Schema Fingerprint, Canonical Argument Byte·Schema, Approval과 Call Budget을 고정된 순서로 검사한다.
- Tool Policy는 Stable Tool ID·Connection·Schema Snapshot에 정확히 묶고 명시 Policy가 없으면 기본 Deny한다. User Connection은 요청자와 Owner가 일치해야 하며 Instance Connection은 사용자 Owner를 갖지 않는다.
- Approval은 Actor, Run, Tool, Argument와 Policy Fingerprint에 묶고 만료와 User/Admin 승인 주체를 검증한다. Destructive Tool의 Admin Approval 적용 범위는 암묵적 기본값 대신 명시 Policy가 결정한다.
- Run·Tool 단위 Call Budget은 정책 Version이 바뀌어도 누적하며 실제 실행을 시도한 호출은 실패해도 환불하지 않는다. Argument는 Canonical JSON UTF-8 Byte로 제한한다.
- Timeout과 Result Byte Limit은 허용된 `GuardedToolCall`에 필수 실행 제한으로 전달한다. 실제 MCP Transport 차단과 영속 Budget Ledger는 WBS-09 Adapter가 소유한다.
- Output Guardrail은 WBS-08의 Direct Answer 또는 Reducer가 만든 최종 Markdown과 Evidence Link를 `OutputCandidate`로 받는다. 입력은 모델 생성 여부와 관계없이 항상 `untrusted`다.
- 처리 순서는 CRLF/CR→LF와 NFC 정규화, 전체 입력 UTF-8 Byte Limit, `core-secret-v1` 중앙 Redaction, Versioned Stack Trace·내부 Path 제거, Raw HTML Angle Bracket Escape, Markdown·Evidence Link 검사, Mention 제한, UTF-8 안전 절단, 빈 출력 거부로 고정한다.
- 허용 Link Scheme, 상대 Link 허용 여부, 일반 Mention 수, Evidence 개수·개별 Byte와 입출력 Byte Limit은 `OutputGuardrailPolicy`에 명시한다. `javascript`, `data`, `file`, `vbscript`는 Allowlist에도 넣을 수 없고 Protocol-relative Link와 허용되지 않은 Scheme은 제거하되 Link Label은 보존한다.
- `@channel`, `@here`, `@everyone` 같은 Broadcast Mention은 항상 전각 `＠`로 중립화하고 일반 Mention은 정책 수를 넘긴 항목만 중립화한다. Channel별 Mention 재활성화는 이 공통 경계 밖에서 허용하지 않는다.
- 허용 결과인 `SafeOutput`은 Sanitized Markdown·Evidence, Content Fingerprint, 정책 Version·Fingerprint와 안전한 변경 수치만 제공한다. 원문 Output·Evidence와 Rule Pattern·Replacement는 `repr`과 오류에 포함하지 않는다.
- Output Guardrail은 답의 의미를 다시 추론하거나 Slack Block으로 변환하지 않는다. WBS-08은 `OutputCandidate`를 만들고 WBS-16 Renderer는 `SafeOutput`만 소비한다.
- WBS-06.4.1은 Framework-free 공통 경계까지만 구현한다. 실제 Orchestrator 조립은 WBS-08, Slack별 구조·분할은 WBS-16, Log·Run Event 적용은 WBS-06.4.2와 WBS-17이 소유한다.
- Log와 Run Event는 `core-telemetry-v1` 정책을 사용한다. 정책은 허용 Log Field, Message·구조화 데이터의 UTF-8 Byte Limit, 최대 깊이와 항목 수를 명시하고 Canonical SHA-256 Fingerprint를 제공한다.
- Telemetry Redaction은 CRLF/CR→LF와 NFC 정규화 뒤에 기존 `core-secret-v1`을 적용한다. 결과에는 Sanitized Payload와 정책·Redaction Summary만 남기고 원문 값은 `repr`과 오류에서 제외한다.
- Logging Filter는 `%` Argument를 한 번만 렌더링한 뒤 Message와 허용 Extra를 Redact한다. 임의 Extra는 버리고 Exception·Stack 원문은 제거하되 Exception Type만 보존한다. Filter 자체가 실패하면 원문 대신 고정된 안전 메시지를 기록한다.
- 모든 SQLite Run Event Insert는 하나의 최종 Writer만 사용한다. 첫 `run.received`, Queue 상태 Event와 범용 Append가 저장 직전에 같은 Redaction을 통과하며, 실패하면 기존 Unit of Work가 상태 변경과 Event를 함께 Rollback한다.
- Run Event Attribute는 Redaction과 별개로 Chain-of-Thought, Provider·Raw Prompt, Attachment Body, Slack 원문 Event, Tool Result와 `secret` Field를 재귀적으로 금지한다. HTTP JSON과 SSE는 저장된 Safe Event만 읽는다.
- WBS-06.4.2는 표준 Logging Filter와 Run Event 최종 Writer까지만 소유한다. JSON Formatter·Metric·Trace·OpenTelemetry는 WBS-17, 과거 Event 일괄 재작성은 Migration 승인 범위로 남긴다.
- 외부 Text는 Source/Trust Metadata를 가진 Envelope로 감싸며 내부 지시로 승격하지 않는다.
- Audit은 Append-only이며 원문 Token/Prompt/Tool Result 대신 Version, Fingerprint와 Redacted Diff를 저장한다.
- WBS-03 Unit of Work 위에서 `audit_events`의 Migration, Append-only 제약과 Repository를 이 WBS가 소유한다.
- `core-audit-v1`은 Actor·Action·Resource·Outcome, 이전·이후 Summary와 Details를 CRLF/NFC 정규화하고 기존 `core-secret-v1`으로 Redact한다. 안전한 각 Summary와 전체 변경에는 Canonical SHA-256 Fingerprint를 남긴다.
- Audit Action은 후속 WBS가 확장할 수 있는 검증된 Namespace 문자열을 사용한다. Actor는 사용자 Lifecycle과 System 작업을 함께 기록할 수 있도록 `users` Foreign Key에 묶지 않는다.
- 모든 Audit Insert는 하나의 최종 SQLite Writer를 사용한다. 상태 변경을 동반하는 Action은 같은 Unit of Work에 Audit Draft를 전달하며 둘 중 하나가 실패하면 전체를 Rollback한다.
- `audit_events`는 Update를 항상 거부하고 365일 Retention 이전 Delete를 차단한다. Table 소유 WBS는 만료 Batch Purge 경계만 제공하고 WBS-19가 실제 Retention Job을 조율한다.
- `GET /api/v1/audit-events`는 활성 Admin만 호출한다. Actor·Action·Resource·Outcome·기간 Filter와 Filter Scope에 묶인 Keyset Cursor를 사용한다.
- 현재 구현된 Bootstrap Grant 발급·회전, 최초 Admin 생성과 Migration 적용을 먼저 Audit한다. 미래 Tool Policy·Skill·Memory·Schedule·API Key·IP Policy Action은 각 소유 WBS가 같은 Writer에 연결한다.
- Security Header, Session Rotation과 Localhost 기본 Bind는 Web Middleware에서 강제한다.

## 구현 체크리스트

- [x] 신뢰 수준, Guardrail Result와 Error Code 계약을 정의한다.
- [x] Input 정규화, 크기/MIME/Rate Rule과 기존 Idempotency 경계 앞의 보호된 Run 제출을 구현한다.
- [x] Versioned 중앙 Redaction Policy·Service와 안전한 Result Summary를 구현한다.
- [x] Tool Policy와 Approval 검증 Engine의 공통 단계를 구현한다.
- [x] Output Sanitizer, Mention/Link Policy와 Secret Redaction을 구현한다.
- [x] External Data Envelope와 Control Character/HTML 정규화를 구현한다.
- [x] Log와 Run Event의 중앙 Redaction Filter·최종 Writer를 구현한다.
- [x] Audit Event Port, SQLite Adapter와 관리자 조회 API 기반을 만든다.
- [x] CSP, Frame, Content-Type, Same-origin과 기본 Bind 정책을 적용한다.
- [ ] Guardrail/Audit 변경을 Eval 영향 대상으로 표시하는 Fingerprint를 만든다.

## 검증 체크리스트

- [x] 비활성 Principal, 대형 입력, 금지 MIME를 차단하고 허용된 중복 요청이 Run 하나로 Replay되는지 확인한다.
- [x] Deny Tool, 다른 사용자 Connection과 승인 없는 Write 호출이 실행되지 않는지 확인한다.
- [x] Secret, Stack Trace, 내부 Path와 Mention 폭주가 출력에서 제거되는지 확인한다.
- [ ] 외부 문서의 지시가 System/Tool Policy를 바꾸지 못하는지 Contract Test를 실행한다.
- [ ] 모든 필수 관리 Action이 Actor와 안전한 Diff를 Audit하는지 확인한다.
- [x] Log와 Run Event 저장·JSON·SSE에 Secret 원문이 없는지 Fixture 기반으로 검사한다.
- [x] Audit Event에 Secret 원문이 없는지 Fixture 기반으로 검사한다.

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

## 2차 구현 결과

- `RedactionRule`, `RedactionPolicy`, `RedactionSummary`와 원문 값을 `repr`에서 제외하는 `RedactionResult` 계약을 추가했다.
- Rule ID·Target·Pattern·Replacement·Flags와 재귀 제한을 Canonical JSON으로 직렬화해 SHA-256 정책 Fingerprint를 계산한다. 실제로 탐지한 Secret은 Fingerprint에 사용하지 않는다.
- `RedactionService`가 Text와 중첩 Mapping·Sequence의 Credential 할당, 알려진 Token Prefix, 민감 Key와 Secret Reference를 같은 정책으로 처리한다. 최대 깊이·항목 수와 Cycle을 안전한 오류 코드로 거부한다.
- 기존 CLI의 `redact_text`, `redact_data`, `render_json` 함수는 유지하고 내부 구현만 `core-secret-v1` 중앙 Service에 위임했다.
- `ExternalDataService`가 Plain Text와 HTML의 입력 Byte Limit, CRLF/NFC, Control/Bidi/Hidden Unicode, 실행·비가시 HTML 제거와 중앙 Redaction을 순서대로 적용한다.
- External Data는 항상 `untrusted`로 고정된다. Envelope의 안전한 Metadata에는 Source Kind, 정책 Version·Fingerprint, Redaction·제거 횟수와 Redaction 완료 Content Fingerprint만 포함한다.
- 전용 Renderer가 Attribute와 Content를 Escape해 `</external_data><system>` 같은 외부 문장이 Envelope 경계를 닫거나 새 Instruction Tag를 만들지 못하게 한다.
- 원문이 다른 두 Secret이 같은 Redaction 결과를 만들면 같은 Content Fingerprint를 생성하는지, 오류·결과 표현에 원문이 없는지 Unit Test로 고정했다.
- 아직 MCP/Web/Model 호출, Log·Run Event와 최종 Output에는 연결하지 않았다. Tool Policy는 3차, Output·Log·Event Redaction은 4차, Append-only Audit은 5차 구현 단위로 남긴다.
- 남은 Guardrail·Audit 작업이 있으므로 WBS-06 상태는 `진행 중`으로 유지한다.

## 3차 구현 결과

- Framework-free `ToolPermission`, `ToolApprovalRequirement`, Connection Scope, Policy Effect와 Tool Guardrail Stage·Outcome·Error Code 계약을 추가했다.
- Tool Policy가 Stable Tool ID, Connection ID, 예상 Schema Fingerprint, Permission·Approval, Run별 호출 수와 Argument·Result Byte·Timeout 제한을 명시하도록 했다. 조직 운영 기본값은 추가하지 않고 모든 필드를 Canonical 정책 Fingerprint에 포함했다.
- Stable Tool Resolver, Policy Provider, Argument Validator, Approval Verifier, 원자적 Budget Ledger와 Tool Executor Port를 정의했다. 실제 MCP·SQLite Adapter는 추가하지 않았다.
- Tool Guardrail은 활성 Principal과 Run 요청자 일치, Stable ID 해석, User Connection Owner, 명시 Policy와 Permission·Schema Drift, Canonical JSON Argument·Byte·Schema, Actor·Run·Tool·Argument·Policy에 묶인 승인, Call Budget을 고정된 순서로 검사한다.
- Policy가 없거나 Deny인 Tool, 비활성·Unknown Tool, 교차 사용자 Connection, Schema Drift, 승인 없는 Write와 만료·Scope 불일치·일반 사용자 Admin 승인을 실패 폐쇄 방식으로 차단한다.
- `GuardedToolExecutionService`가 모든 검사를 통과한 호출만 Executor에 넘긴다. Timeout과 Result Byte Limit은 필수 실행 계약으로 전달하고 차단 호출의 Executor 호출 수를 0회로 고정했다.
- Run·Tool 호출 횟수는 Policy Version 변경으로 초기화하지 않으며, 실행 실패도 예약된 호출 횟수를 환불하지 않는 계약으로 정했다.
- Argument와 Approval Reference, Connection ID·Owner와 실제 Tool Name은 요청·정책·해석 결과·오류의 `repr`에서 제외한다. 판정에는 Stable Tool ID, 정책 Version·Fingerprint, Permission과 안전한 Byte·Call Metadata만 남긴다.
- 실제 Registry, JSON Schema Adapter, Approval·Invocation 영속화, MCP Timeout·Result Stream Byte 차단과 Tool Result 정규화는 WBS-09에 남긴다.
- Output·Log·Run Event Redaction과 Append-only Audit이 남아 있으므로 WBS-06 상태는 `진행 중`으로 유지한다.

## 4차 1단계 구현 결과

- Framework 의존성이 없는 Output Guardrail Stage·Outcome·Error Code와 `OutputCandidate`, `SafeOutput`, 정책·판정·변경 Summary 계약을 추가했다.
- 모든 입출력·Evidence·Mention Limit, 허용 Link Scheme·상대 Link 여부, Broadcast Mention, Stack·Path Rule과 절단 Marker를 `OutputGuardrailPolicy`에 명시하고 Canonical SHA-256 정책 Fingerprint를 계산한다. 조직 공통 기본값은 추가하지 않았다.
- `OutputGuardrailService`가 CRLF/NFC와 UTF-8 Byte Limit을 적용한 뒤 기존 `core-secret-v1` Redaction Service를 Markdown과 Evidence에 함께 사용한다.
- Python Traceback, Node Stack Frame, Unix·Windows 내부 Path를 Versioned Rule로 제거하고 Raw HTML·Slack Angle Markup을 Escape한다. Pattern과 Replacement는 계약 표현에 노출하지 않는다.
- Markdown Inline·Reference Link와 Evidence Link가 같은 Scheme 정책을 사용한다. 허용된 HTTPS·상대 Link는 보존하고 `javascript`, `data`, `file`, Protocol-relative Link는 제거하며 Inline Link Label은 남긴다.
- Broadcast Mention은 항상 중립화하고 일반 Mention은 정책 수를 넘긴 항목만 중립화한다. 한국어·Emoji를 자르지 않는 UTF-8 Byte 절단과 안전한 Marker를 적용한다.
- 동일 입력·정책은 동일한 Sanitized Content·Fingerprint·Summary를 만들고, 이미 Sanitized된 Markdown을 다시 처리해도 내용이 변하지 않도록 Unit Test로 고정했다.
- 아직 WBS-08의 `AgentResult`·Reducer, WBS-16의 Slack Renderer에 조립하지 않았다. 구조화 Log·Run Event Redaction은 WBS-06.4.2, Append-only Audit은 WBS-06.5로 남아 있으므로 WBS-06 상태는 `진행 중`이다.

## 4차 2단계 구현 결과

- Framework 의존성이 없는 `TelemetryRedactionPolicy`, 안전한 Log·Run Event Payload, 변경 Summary와 안정적인 Error Code 계약을 추가했다.
- `core-telemetry-v1`은 Log·Event Message와 구조화 데이터의 UTF-8 Byte Limit, 재귀 깊이·항목 수, 허용 Log Field를 명시한다. 같은 정책은 같은 Canonical SHA-256 Fingerprint를 만든다.
- `TelemetryRedactionService`가 CRLF/NFC 정규화와 `core-secret-v1` Redaction을 순서대로 적용한다. 금지 Event Field, 크기 초과, Cycle과 잘못된 구조는 원문을 포함하지 않는 오류로 실패 폐쇄한다.
- 표준 Logging Filter가 `%` Argument와 허용 Extra를 Redact하고 임의 Extra, Exception 원문과 Stack을 제거한다. Exception Type은 보존하며 Filter 실패 시 고정된 안전 메시지만 남긴다.
- `SqliteRunEventWriter`를 유일한 Event Insert 경계로 만들었다. 첫 `run.received`, Queue 전이·복구 Event와 범용 Append가 저장 직전에 같은 Redaction을 통과한다.
- Event Redaction 실패는 기존 SQLite Unit of Work를 그대로 Rollback한다. 동시 Index와 Visibility 계약은 유지하고 JSON API와 SSE는 Redaction 완료 Event만 전달한다.
- 정책 결정성·정규화·크기·Cycle·금지 Field, Logging Argument·Extra·Exception·Fallback, SQLite 전체 쓰기 경로와 JSON·SSE 비노출을 Unit·Architecture·Integration Test로 고정했다.
- JSON Formatter, Metric, Trace와 OpenTelemetry는 WBS-17에 남겼다. 기존 DB Event를 일괄 재작성하거나 Schema·OpenAPI를 변경하지 않았다.

## 5차 구현 결과

- Framework 의존성이 없는 `AuditEvent`, `AuditOutcome`, Versioned `AuditPolicy`, 안전한 Draft·Query·Cursor 계약을 추가했다.
- `core-audit-v1`이 이전·이후 Summary와 Details의 CRLF/NFC, JSON 호환 형태, 크기·깊이·항목 수를 검증한 뒤 기존 `core-secret-v1`으로 Redact한다. 안전한 각 Summary, Details와 전체 변경에 Canonical SHA-256 Fingerprint를 남긴다.
- `0004_audit_events.sql`이 `audit_events` Table과 Actor·Action·Resource·Outcome·시간 조회 Index를 추가했다. Update를 항상 거부하고 365일이 지나지 않은 Event Delete를 차단한다.
- 단일 `SqliteAuditWriter`가 모든 Audit Insert를 저장 직전에 Redact한다. 별도 `SqliteAuditStore`는 관리자 조회와 WBS-19가 사용할 만료 Batch Purge만 제공한다.
- Bootstrap Grant 발급·회전, 최초 Admin 생성과 Migration 적용을 현재 관리 Action으로 연결했다. No-op은 Event를 만들지 않으며 상태 변경과 Audit 실패는 같은 Transaction에서 Rollback한다.
- Audit Actor는 `users` Foreign Key에 묶지 않는다. `system.bootstrap`, `system.migration` 같은 System Actor와 사용자 Lifecycle 이후의 과거 Event를 보존한다.
- 활성 Admin 전용 `GET /api/v1/audit-events`를 추가했다. Actor·Action·Resource·Outcome·기간 Filter와 Filter·Admin Scope에 묶인 `(created_at DESC, id DESC)` Cursor를 지원한다.
- OpenAPI Artifact와 Frontend 생성 Type을 갱신하고 정책 결정성·Secret 비노출·Append-only·Retention·원자성·권한·Cursor를 Unit·Integration·Contract·Architecture Test로 고정했다.
- React Audit Log 화면과 실제 Retention Job은 후속 WBS가 소유한다. 아직 구현되지 않은 필수 관리 Action의 연결도 각 기능 WBS가 맡으므로 WBS-06 상태는 `진행 중`으로 유지한다.

## 완료 조건

- Guardrail 차단 요청은 Root/Provider/Tool을 호출하지 않는다.
- Unknown/Denied Tool과 교차 사용자 Token 실행이 0건이다.
- Secret Leak과 Chain-of-Thought 저장·노출이 0건이다.
- 필수 보안·관리 변경이 누락 없이 Append-only Audit에 기록된다.

## 미결정 사항

- 조직별 기본 Rate Limit Profile
- Secret Pattern Set의 업데이트와 오탐 승인 절차
- Destructive Tool의 2차 Admin Approval 적용 범위
