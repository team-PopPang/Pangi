# WBS-07 Model Routing과 Egress Policy

## 요약

Root, Subagent, Skill, Eval이 사용하는 모델 호출을 Provider Adapter 뒤로 모으고, 데이터 분류·목적·Region·Retention에 따라 호출 전 Egress Policy를 결정한다.

## 목표

- Provider 세부사항을 Core 계약에서 분리한다.
- 모든 모델 호출 전에 Data Class와 Source Kind를 계산한다.
- 허용되는 Provider/Model/Region/목적 조합만 실행한다.
- Logical Call과 Network Request Retry를 분리해 기록한다.

## 선행 작업

- WBS-02
- WBS-03
- WBS-06

## 설계서 참조

- [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md): Section 0.1, 8.5, 18.3~18.4, 21.1, 22.1, 23.2, 26

## 내부 구현 단계

WBS 번호와 문서는 유지하고 아래 실행 단위를 독립 PR로 구현한다. 단계 수와 범위는 고정값이 아니다. 새로운 Provider 경계나 별도 검증 Gate가 필요해 분해를 구체적으로 바꿔야 하면 영향 범위를 정리해 사용자 또는 제품 책임자의 승인을 받은 뒤 조정한다.

1. **Model 호출 계약과 Egress Policy 결정 경계(WBS-07.1)**: Model 요청·응답·오류, Versioned Profile·Egress Policy, Data Class 합성, 후보 필터와 Redaction 선행 실행 경계를 구현한다.
2. **선택 설치 Provider Adapter와 Retry 계약(WBS-07.2)**: OpenAI·Bedrock 선택 설치 Skeleton, 구조화 출력 검증과 Transport Retry 경계를 구현한다.
3. **Model Policy·Invocation 영속화와 계측(WBS-07.3)**: 정책·호출 Migration, Logical Call·Provider Request·Token·Duration과 안전한 결정 Event를 구현한다.
4. **Model Policy 조회·영향 Eval 연동·활성화 Gate API(WBS-07.4.1)**: Version 식별 관리 API, 안전한 Diff·Audit와 WBS-15 Eval 활성화 Gate 연동점을 구현한다.
5. **Model Policy Dashboard(WBS-07.4.2)**: Policy·사용처 연동 상태와 최근 허용/거부 Summary를 조회하는 관리자 화면을 구현한다.

## 범위

- Model Provider Port와 OpenAI/Bedrock 선택 Adapter 기반
- Model Profile, Request Policy와 Egress Policy
- Data Classification, Redaction과 Candidate 선택
- 구조화 출력과 Provider Retry 계측
- Model Policy 관리/영향 분석 API 기반

## 범위 밖

- Root Prompt와 Decision 검증
- Subagent Tool Loop
- Semantic Grader의 평가 기준
- 실제 운영 Model 이름의 문서 고정

## 기술 설계

- 모든 호출자는 목적(`orchestration|subagent|skill|eval|red_team`)과 Source Kind를 명시한 Model Request를 만든다.
- Data Class 민감도는 `public < internal < confidential < personal < restricted` 순서로 고정한다. Policy Engine은 모든 입력 Source의 Class 집합과 가장 높은 Class를 함께 계산한다.
- 후보 Profile은 요청에 포함된 모든 Data Class와 Source Kind를 지원해야 한다. 하나라도 허용하지 않으면 후보에서 제거한다.
- 하나의 논리 Profile은 명시적인 `routing_priority`가 서로 다른 물리 후보를 제공한다. 중복 ID나 우선순위가 있으면 숨은 Tie-break를 적용하지 않고 실패 폐쇄한다.
- Region, Zero-retention, Raw Content 허용 여부와 Redaction 요구를 검사한 뒤 Adapter를 선택한다.
- Region Allowlist가 비어 있으면 Region이 없는 Profile만 허용한다. Region 값이 있으면 Allowlist에 정확히 포함돼야 한다.
- 허용 요청도 중앙 Redaction을 항상 통과한다. `require_redaction`은 정책의 최소 요구를 나타내며 Redaction 우회를 허용하는 Switch가 아니다.
- 허용 후보가 없으면 임의 Provider Fallback 없이 `model_policy_denied`로 실패한다.
- 원문 Prompt 대신 Policy/Fingerprint/Data Class/Redaction Count/Token/Duration을 저장한다.
- WBS-03 Unit of Work 위에서 `model_policies`, `model_invocations`의 Migration, 제약과 Repository를 이 WBS가 소유한다.
- Transport Retry는 같은 Logical Call의 `provider_request_count`만 증가시키며 결과 뒤 Semantic Retry는 허용하지 않는다.
- Policy 변경은 영향받는 Prompt/Skill/Subagent와 필수 Eval Suite를 계산한 뒤 활성화한다.

## 구현 체크리스트

- [x] Provider Port, 구조화 출력 Request/Response와 Error 계약을 정의한다.
- [x] Model Profile과 Versioned Egress Policy Domain을 구현한다.
- [x] Source Kind별 Data Class 합성과 Redaction Pipeline을 구현한다.
- [x] Provider/Model/Region/Purpose/Retention 후보 필터를 구현한다.
- [x] OpenAI와 Bedrock Adapter의 선택 설치 Skeleton을 만든다.
- [x] Logical Call, Provider Request, Token, Duration과 정책 결정 Event를 기록한다.
- [x] Policy 영향 분석, Eval 실행 연동과 활성화 API를 구현한다.
- [ ] Dashboard에서 Policy, 사용처와 최근 허용/거부 Summary를 조회할 기반을 만든다.

## 검증 체크리스트

- [x] Data Class/Provider/Region 조합별 Allow/Deny Matrix를 Unit Test로 고정한다.
- [x] Redaction 전 금지 Field가 Provider Adapter에 도달하지 않는지 확인한다.
- [x] 허용 후보가 없을 때 Provider 호출이 0건인지 확인한다.
- [x] Network Retry와 Logical Call 수가 분리되는지 Contract Test를 실행한다.
- [x] Provider가 잘못된 구조화 출력을 반환할 때 안전하게 실패하는지 확인한다.
- [x] Policy 변경이 영향 Eval 없이 활성화되지 않는지 확인한다.

## 1차 구현 결과

- Framework-free `DataClass`, Model Purpose·Retention과 정책 Stage·Outcome·Error Code를 추가했다.
- Data Class 민감도 순서를 고정하고 여러 Source의 전체 Class 집합과 최고 등급을 함께 계산한다. Candidate는 요청의 모든 Class와 Source Kind를 지원해야 한다.
- `ModelProfile`은 Provider, Model, Region, 지원 Class·Source·Purpose, Retention, Raw Content와 명시적 Priority를 Versioned SHA-256 Fingerprint에 포함한다.
- `ModelEgressPolicy`는 허용 Provider·Model·Region·Class·Source·Purpose와 Redaction·Zero-retention·Raw Content 조건을 Canonical Fingerprint로 고정한다.
- Profile과 Egress Policy를 기존 `PolicyFingerprintReference`로 변환해 WBS-15의 영향 Snapshot에 포함할 수 있다.
- `ModelPolicyService`는 Policy를 먼저 확인한 뒤 후보를 필터링한다. 후보가 없거나 Candidate ID·Priority가 중복되면 `model_policy_denied`로 실패하고 Provider를 호출하지 않는다.
- 허용된 모든 Source는 기존 중앙 Redaction을 통과한다. Provider Port에는 Redaction 완료 Content, 안전한 Input Fingerprint와 정책 결정 Metadata만 전달한다.
- Prompt, Output Schema와 구조화 Provider Output은 객체 표현과 오류에서 제외한다. 실제 OpenAI·Bedrock SDK, SQLite, HTTP와 UI 계약은 변경하지 않았다.
- Provider·Model·Region·Purpose·Source Kind·Data Class·Retention·Raw Content Matrix, Region 없는 Profile, 우선순위 충돌, Secret 비노출과 Provider 호출 0건을 Unit·Contract Test로 고정했다.
- Adapter, Retry·사용량 계측, 영속화와 관리 API가 남아 있으므로 WBS-07 상태는 `진행 중`으로 유지한다.

## 2차 구현 결과

- `ModelInputSource`에 System과 User 역할을 추가하고 System Source가 User Source보다 먼저 오도록 Provider 공통 순서를 고정했다.
- Provider 응답은 Token Usage, 실제 Provider Request 수, 전체 Duration, Provider Latency와 정규화된 종료 사유를 공통 계약으로 반환한다.
- `ProviderRetryPolicy`는 최대 요청 횟수, 요청별 Timeout, 전체 Timeout과 요청 사이 Backoff를 명시한다. 조직 기본값은 아직 고정하지 않았다.
- `RetryingModelProvider`는 Timeout, Rate Limit과 일시적인 Provider 장애만 재시도한다. SDK 내부 Retry는 끄고 Pangi가 실제 Network Request 수를 직접 계산한다.
- OpenAI 선택 Adapter는 Responses API, Strict JSON Schema와 `store=False`를 사용한다. `max_retries=0`으로 SDK Retry를 비활성화한다.
- Bedrock 선택 Adapter는 Converse API의 `outputConfig.textFormat`을 사용한다. Boto3는 `total_max_attempts=1`로 설정하고 동기 호출을 Event Loop 밖에서 실행한다.
- Provider가 반환한 JSON은 `StructuredOutputValidator` Port를 통해 요청 Schema로 다시 검사한다. JSON 파싱·Schema 검증·종료 사유가 잘못되면 Semantic Retry 없이 실패한다.
- OpenAI·Boto3·JSON Schema Package는 선택 Extra와 개발 Extra에만 포함한다. 기본 `pangi` Import는 이 Package를 불러오지 않는다.
- Fake OpenAI·Bedrock Client와 결정적 Clock·Sleeper를 사용해 실제 Credential이나 외부 Network 없이 요청 변환, 오류 정규화, Retry와 사용량 변환을 검증했다.
- 호출 Metadata는 이번 단계에서 안전한 응답·오류 계약으로만 반환한다. SQLite 영속화와 정책 결정 Event는 WBS-07.3에서 구현한다.
- Credential·Model 설정과 Runtime 조립, Provider Capability 자동 탐색은 남아 있으므로 WBS-07 상태는 `진행 중`으로 유지한다.

## 3차 구현 결과

- SQLite Migration 5는 `model_policies`와 `model_invocations`를 추가한다. Policy Version 중복, Policy별 단일 Active Version, Active 규칙 변경·삭제와 잘못된 Invocation 상태를 DB 제약으로 거부한다.
- `ModelPolicySnapshot`은 Egress Policy와 우선순위가 고정된 후보 Profile을 Canonical JSON으로 묶고 SHA-256 Fingerprint를 계산한다. Repository는 Draft Version을 추가하고 Active Version만 Routing에 제공한다.
- Model 실행자는 실제 Provider 호출 전에 Run과 선택적인 Step에 연결된 `running` Invocation과 `model.policy_allowed` 내부 Event를 저장한다. 이 기록이 실패하면 Provider를 호출하지 않는다.
- Policy가 요청을 차단하면 Provider 호출 없이 `denied` Invocation과 `model.policy_denied` 내부 Event를 같은 Unit of Work에 저장한다.
- Provider 호출은 SQLite Transaction 밖에서 실행한다. 호출이 끝나면 Invocation 상태와 `model.invocation_completed` Event를 다시 하나의 Unit of Work로 저장한다.
- 하나의 Logical Call은 항상 `logical_calls=1`로 기록한다. Transport Retry는 같은 Invocation의 `provider_requests`만 증가시킨다. 같은 Run에서 같은 Logical Call을 다시 실행하려 하면 Provider 호출 전에 거부한다.
- Token Usage, 전체 Duration, Provider Latency, Finish Reason과 정규화된 오류 코드를 저장한다. Token을 제공하지 않는 Provider 응답은 Token Field를 `NULL`로 유지한다.
- 원문 Logical Call ID는 저장하지 않고 Fingerprint만 남긴다. Prompt, Redaction 이후 실제 입력, 구조화 Model Output과 Credential은 SQLite와 Run Event에 저장하지 않는다.
- 영속화 실패 때문에 Provider를 다시 호출하지 않는다. Provider 응답 뒤 완료 기록이 실패하면 결과를 반환하지 않고 안전하게 실패한다.
- Migration·Repository·허용·차단·Retry·Rollback·중복 Logical Call과 Secret 비노출을 Unit·Contract·Integration Test로 검증했다.
- Model Policy 관리 API·Dashboard, 활성화·폐기 흐름과 WBS-15 Eval Gate가 남아 있으므로 WBS-07 상태는 `진행 중`으로 유지한다.

## 4차 구현 결과

- WBS-07.4를 Backend 관리 경계인 WBS-07.4.1과 Dashboard인 WBS-07.4.2로 분리했다. SQLite 활성화 Transaction과 첫 실제 관리 화면의 Routing·상태 처리를 독립적으로 검증하기 위한 조정이며 WBS-07 전체 범위는 바꾸지 않았다.
- SQLite Migration 6은 모든 신규 Model Invocation에 `requested_profile` 기록을 요구한다. 기존 Row는 연결된 Policy Version으로 안전하게 Backfill하고, Profile·기간 기준 최근 허용/거부 집계 Index를 추가했다.
- `GET /api/v1/model-policies`는 Policy Version, 상태, 안전한 Egress/Profile Summary, 최근 7일 허용·거부·목적·거부 이유와 Candidate 영향 정보를 Keyset Cursor로 반환한다.
- Policy Version은 `(policy_id, version)` 복합 식별자다. 평가와 활성화 API를 `/api/v1/model-policies/{policy_id}/versions/{version}/evaluate|activate`로 명확하게 분리했다.
- `model-policy-impact-v1`은 Active Baseline과 Draft Candidate의 Egress Policy·Profile Reference를 비교한다. 최초 활성화는 Baseline을 억지로 만들지 않고 모든 Candidate Key가 추가된 영향으로 표현한다.
- Prompt·Skill·Subagent Registry가 아직 없으므로 사용처와 필수 Eval Suite를 빈 결과로 확정하지 않는다. API는 `consumer_resolution=unavailable`을 반환하고 실제 선택·실행·Snapshot 영속화는 WBS-15가 소유한다.
- `ModelPolicyEvaluationGateway`는 Eval 요청과 승인 확인만 정의한다. 현재 Runtime은 실패 폐쇄 Adapter를 조립하므로 WBS-15가 연결되기 전에는 Eval 요청과 활성화를 `model_policy_eval_unavailable`로 거부한다.
- 활성화 요청은 Candidate Fingerprint, Impact Fingerprint와 Eval Run ID를 다시 확인한다. 승인된 경우에만 기존 Active 폐기, Candidate 활성화, Eval Run 연결, `model_policy.version_activated` Audit Event와 Idempotency 결과를 하나의 Transaction으로 저장한다.
- 동일한 `Idempotency-Key`와 요청 Fingerprint는 기존 활성화 결과를 재생하고 다른 요청은 충돌로 거부한다. Audit 저장 실패는 Policy 상태와 Idempotency Record까지 모두 Rollback한다.
- 관리자 권한, Same-origin·CSRF, Cursor·OpenAPI·Error Envelope와 원문 Prompt·출력·Credential 비노출을 Unit·Integration·Contract Test로 고정했다.
- Model Policy Dashboard가 남아 있으므로 WBS-07 상태는 `진행 중`으로 유지한다.

## 완료 조건

- 모든 모델 호출이 Versioned Egress Policy를 통과한다.
- 금지된 Data Class/Provider 조합의 Provider 호출이 0건이다.
- Logical Call과 실제 Provider Request 수를 Trace에서 구분한다.
- 원문 Prompt와 금지 데이터가 Invocation 기록에 저장되지 않는다.

## 미결정 사항

- 설치 시 기본 Provider와 Model Profile
- Provider별 Zero-retention Capability Discovery 방식
- 비용·지연 기반 후보 우선순위의 초기값
