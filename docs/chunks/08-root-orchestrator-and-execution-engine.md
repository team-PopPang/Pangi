# WBS-08 Root Orchestrator와 실행 Engine

## 요약

자연어 요청을 Root 모델 한 번으로 Direct/Delegate/Skill Decision으로 바꾸고, 서버가 검증한 DAG만 실행해 결과를 결정적으로 합성한다.

## 목표

- 일반 자연어 요청당 Root logical call을 정확히 1회로 제한한다.
- Input Guardrail 차단과 명시 Skill은 Root를 호출하지 않는다.
- 잘못된 Decision을 Tool/Subagent 실행 전에 거부한다.
- Task 결과를 안정적인 `AgentResult`와 Evidence로 합성한다.

## 선행 작업

- WBS-05
- WBS-06
- WBS-07

## 설계서 참조

- [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md): Section 4.3~4.5, 7.1~7.5, 8.1~8.2, 9.1~9.3, 9.6, 23.5, 24 Phase 1

## 내부 구현 단계

WBS 번호와 문서는 유지하고 아래 실행 단위를 독립 PR로 구현한다. 단계 수와 범위는 고정값이 아니다. 새로운 실행 경계나 별도 검증 Gate가 필요해 분해를 구체적으로 바꿔야 하면 영향 범위를 정리해 사용자 또는 제품 책임자의 승인을 받은 뒤 조정한다.

1. **Orchestration 계약과 Plan Validator(WBS-08.1)**: Direct/Delegate/Skill Decision, DelegatedTask, AgentResult와 Evidence 계약, Mode·Registry·DAG·Limit 검증과 결정적 위상 정렬을 구현한다.
2. **Root Context Builder와 단일 Decision 호출(WBS-08.2)**: 최소 Catalog, Decision Schema와 사용자 Data Envelope를 구성하고 주입된 Model 실행 경계로 Root logical call을 한 번만 수행한다.
3. **Run Step 영속화와 Dependency Execution Engine(WBS-08.3)**: 검증된 Plan을 Run Step으로 저장하고 의존성·동시 실행 제한·Required/Optional 실패 규칙에 따라 실행한다.
4. **Deterministic Reducer와 안전한 합성(WBS-08.4)**: 결과·Evidence를 결정적으로 정렬·중복 제거하고 사전 계획 Synthesis와 Output Guardrail을 연결한다.
5. **Orchestration Run 생명주기와 SafeOutput 영속화(WBS-08.5.1)**: `received → planning → queued → running → composing → completed|failed` 전이, Decision Event, Queue Handler와 최종 Output 저장을 구현한다.
6. **Model Provider와 Root Catalog Runtime 조립(WBS-08.5.2)**: 활성 Model Policy, 선택 Provider와 Principal 범위 Catalog를 실제 Root 실행 경계에 조립한다.
7. **Guarded Run API와 Queue Runtime 통합(WBS-08.5.3)**: 보호된 Run 생성 API, 신뢰된 Data Class 분류, Queue Wake-up과 ASGI 생명주기를 하나의 실행 경로로 연결한다.

## 범위

- `OrchestratorDecision`, `DelegatedTask`, `AgentResult`, Evidence 계약
- Root Context Builder와 Direct/Delegate/Skill Mode
- Decision Schema와 DAG/Registry/Limit Validator
- Execution Engine의 Dependency 실행 기반
- Deterministic Reducer와 선택형 사전 계획 Synthesis 연결점

## 범위 밖

- Subagent 내부 Tool Loop와 Web Search
- Skill DAG Compiler와 Scheduler
- Slack Rendering
- Root가 Tool Result를 보고 다시 계획하는 Loop

## 기술 설계

- Root Context에는 Subagent/Skill/Connection의 최소 Catalog와 Decision JSON Schema만 넣고 Tool Schema/Memory 원문은 제외한다.
- Root Context의 Catalog는 Principal 범위에서 한 번 조회한 불변 Snapshot을 사용한다. 같은 Snapshot에서 Prompt Data와 Validator 허용 이름을 파생해 호출 중 Registry 변경의 영향을 차단한다.
- Catalog 종류별 항목은 최대 100개, Canonical Data는 최대 100KB로 제한한다. 설명과 Trigger는 고정 System 규칙이 아니라 구조화된 Data로 전달한다.
- 사용자 요청 Data Class는 신뢰된 호출자가 비어 있지 않은 집합으로 명시한다. Root가 임의로 `public`이나 `internal`을 선택하지 않는다.
- Direct는 `direct_answer`만, Delegate는 1~5개의 DAG Task만, Skill은 활성 이름 하나만 허용한다.
- Hint는 권한으로 사용하지 않고 Execution 단계에서 Policy와 현재 Connection을 다시 검사한다.
- Dependency Cycle, Unknown Subagent, 초과 Budget과 부적절한 Synthesis를 Plan Validator가 거부한다.
- Delegate Task는 운영 기본 3개, 절대 최대 5개로 제한한다. Task Timeout은 최대 180초, Run Timeout은 최대 600초이며 실제 Plan은 주입된 운영 제한도 함께 지킨다.
- Run Budget은 DAG의 Task Timeout을 단순 합산하지 않고 가장 긴 Dependency 경로의 합으로 검증한다.
- 같은 시점에 실행 가능한 Task가 여러 개면 최초 Decision의 Task 순서를 Tie-breaker로 사용해 위상 정렬 결과를 결정적으로 만든다.
- Root가 만든 Delegate Task는 서버가 기본 `required`, `non-idempotent` Step으로 Materialize한다. 모델 출력은 Optional 여부나 재실행 안전성을 부여하지 못하며 후속 Registry·Skill Compiler만 신뢰된 Metadata를 확장한다.
- 검증된 Plan, Canonical Task 정의와 Redaction 완료 `AgentResult`를 SQLite에 저장한다. Provider Prompt, Tool Result 원문과 비공개 추론은 실행 저장소에 넣지 않는다.
- Step 쓰기는 Run의 `running` 상태, Worker ID와 유효 Lease를 함께 검사한다. 취소·복구와 경합한 오래된 Worker는 Result와 Run 상태를 갱신하지 못한다.
- Dependency가 모두 완료된 Step만 최초 Plan 순서로 준비하고 주입된 1~5개 동시 실행 상한 안에서 실행한다. Task Timeout과 Executor 오류는 Semantic Retry 없이 안정적인 실패 Result로 변환한다.
- `synthesis_subagent`는 최초 DAG에 포함된 단일 `synthesis` Task가 둘 이상의 선행 Task에 의존하고 다른 Task의 선행 조건이 되지 않을 때만 허용한다.
- Root 결과를 받은 뒤 의미를 바꾸기 위한 재호출이나 Semantic Retry를 금지한다.
- Reducer는 Dependency 순서, Evidence URI Dedup, Warning/실패 Source와 Citation을 결정적으로 구성한다.
- 모순 해석이 필요하면 최초 Decision에 포함된 Synthesis Task만 실행한다.
- Direct Answer와 Reducer 결과는 같은 `OutputCandidate`로 변환해 WBS-06.4.1 Output Guardrail을 통과시킨다. Channel Port에는 원문 Candidate가 아니라 허용된 `SafeOutput`만 전달한다.
- Reducer는 입력 Result Tuple 순서를 신뢰하지 않고 Task ID로 최초 Plan 순서를 복원한다. 중복·알 수 없는 Result, Required Result 누락, 실패 Run과 Mode·Outcome 불일치는 합성 전에 안정적인 Error Code로 거부한다.
- Evidence URI는 CRLF와 Unicode NFC를 정규화하고 Padding을 제거한 값을 기준으로 첫 항목만 유지한다. URI가 없는 Evidence는 독립 Source로 보존하며 Evidence Excerpt와 Fact를 새로운 사실처럼 본문에 확장하지 않는다.
- Deterministic 합성은 성공·Partial Summary를 Plan 순서로 배치하고 Task Source가 붙은 Warning과 Source Citation을 구성한다. Synthesis 합성은 최초 DAG의 Terminal Synthesis Result만 본문으로 사용하되 전체 Plan의 Evidence와 Warning은 보존한다.
- `OrchestrationOutputComposer`는 Reducer가 만든 원문 Candidate를 내부에서만 다루고 기존 Output Guardrail의 `SafeOutput`만 반환한다. Guardrail 차단과 예상하지 못한 실패의 오류 표현에는 Candidate 원문을 포함하지 않는다.

## 구현 체크리스트

- [x] Decision/Task/AgentResult/Evidence Schema를 정의한다.
- [x] Root Prompt/Context Builder와 사용자 입력 Data Envelope를 구현한다.
- [x] Trigger별 Root logical call Budget을 구현한다.
- [x] Mode별 XOR, DAG, Registry, Task/Timeout/Composition Validator를 구현한다.
- [x] Direct Result 경로와 Delegate 실행 Plan을 구현한다.
- [x] Dependency 기반 Execution Engine과 Required/Optional 실패 처리를 구현한다.
- [x] Deterministic Reducer와 Synthesis Task 연결을 구현한다.
- [x] Decision, Logical Call, Provider Request와 Validation 실패 Event를 기록한다.
- [x] `SafeOutput` 저장과 `composing → completed|failed` 전이를 원자적으로 처리한다.
- [x] 활성 Model Policy가 선택한 Provider와 실패 폐쇄 Root Catalog를 실제 Root 실행 경계에 조립한다.
- [ ] API의 Run 생성 경로를 Orchestrator Use Case에 연결한다.

## 검증 체크리스트

- [x] 일반 자연어, Guardrail 차단, 명시 Skill별 Root 호출 수를 검증한다.
- [x] 잘못된 Mode XOR, Cycle, Unknown Subagent와 초과 Task를 거부한다.
- [x] Decision 실패 뒤 Tool/Subagent 호출이 0건인지 확인한다.
- [x] Provider Transport Retry가 Logical Call을 늘리지 않는지 확인한다.
- [x] Optional 실패와 Required 실패의 Run 상태/출력이 다른지 확인한다.
- [x] 동일 AgentResult 입력이 동일 Reducer 출력을 만드는지 Property Test를 실행한다.
- [x] Root 재계획과 Delegation Depth 증가를 불변식 테스트로 차단한다.

## 완료 조건

- 자연어 Direct/Delegate 요청의 Root logical call이 정확히 1회다.
- Guardrail 차단과 명시 Skill 요청의 Root logical call이 0회다.
- 잘못된 Decision은 외부 실행 전에 실패한다.
- 결과와 Evidence가 재현 가능한 순서와 형식으로 합성된다.

## 1차 구현 결과

- Framework-free 불변 계약으로 `OrchestratorDecision`, `DelegatedTask`, `Evidence`, `AgentResult`를 추가했다. 모델이 만든 답변·목표·요약·근거 본문·Fact와 Warning은 객체 표현에서 제외한다.
- `Evidence`와 `AgentResult`를 최상위 `pangi` 공개 API로 제공한다. 기본 Package Import는 Provider SDK나 선택 의존성을 불러오지 않는다.
- `OrchestratorPlanValidator`는 Direct/Delegate/Skill Payload XOR, 활성 Skill, 등록 Subagent, Task 수와 Timeout을 외부 실행 전에 실패 폐쇄 방식으로 검증한다.
- 중복 Task와 Dependency, 자기 참조, 알 수 없는 Dependency, Cycle과 중복 Hint를 거부한다. Hint는 Registry 권한으로 간주하지 않고 후속 Tool Policy 검증 대상으로 유지한다.
- DAG의 Critical Path Timeout을 Run Budget과 비교하고 최초 Decision 순서를 Tie-breaker로 사용해 결정적인 위상 정렬 결과를 만든다.
- Synthesis는 등록된 `synthesis` Task 하나가 둘 이상의 선행 결과를 받고 DAG의 Terminal Sink일 때만 허용한다.
- 실제 Root Context·Model 호출, Run Step 저장·실행, Reducer, Output Guardrail과 Run 생성 API 연결은 후속 단계로 유지한다.

## 2차 구현 결과

- `RootCatalogSnapshot`은 Principal 범위의 Subagent·Skill·Connection 최소 설명을 불변 Tuple로 정렬한다. Prompt와 Plan Validator는 같은 Snapshot을 사용하며 종류별 100개와 Canonical 100KB 상한을 넘으면 Model 호출 전에 실패한다.
- `root-orchestration-v1` System 규칙은 Root가 한 번만 계획하고 Tool 실행·재계획·비공개 사고 과정 노출을 하지 않도록 지시한다. Catalog 설명과 사용자 요청은 고정 규칙에 섞지 않고 Canonical JSON Data로 전달한다.
- 사용자 Envelope는 정규화된 Text, Channel과 Attachment의 표시 Metadata만 포함한다. Principal·Request·Idempotency·Thread·Attachment Reference와 Fingerprint는 Root 입력에서 제외한다.
- `orchestrator-decision-v1` JSON Schema는 모든 Field와 `additionalProperties=false`, Task·Timeout·Hint Hard Limit을 고정한다. Framework-free Parser가 Canonical JSON을 `OrchestratorDecision`으로 변환하고 원문 없는 안정적인 오류만 반환한다.
- `RootOrchestratorService`는 Catalog를 요청당 한 번 읽는다. 명시 Skill은 Model 호출 0회로 검증하고, 일반·Scheduler 자연어는 결정적인 `root-orchestration:<run_id>` Logical Call을 기존 Egress·Redaction·Invocation 경계로 정확히 한 번 보낸다.
- Provider Transport Retry는 하나의 Root Model Executor 호출 결과로 계측한다. Model·Parser·Plan 실패 뒤 Semantic Retry는 수행하지 않는다.
- 실제 Registry Adapter와 Provider Runtime 조립, Run 상태 전이·Queue Handler·실행 Engine은 후속 단계로 유지한다.

## 3차 구현 결과

- Framework-free `ExecutionPolicy`, `PreparedExecutionPlan`, `PreparedExecutionStep`, Snapshot·Request·Outcome 계약을 추가했다. 검증된 Root Plan은 Direct 또는 Dependency 순서가 고정된 Delegate 실행 Plan으로만 변환하며 Skill 실행은 후속 Skill Runtime에 남긴다.
- `0007_orchestration_execution.sql`이 불변 `run_execution_plans`와 Canonical Task·Result 저장 Column을 추가한다. 기존 Migration은 수정하지 않으며 같은 원문 Plan Fingerprint Replay만 허용하고 다른 Plan 덮어쓰기를 거부한다.
- Plan, 최초 Step, Queue 전이와 Event를 같은 Transaction에 저장한다. Plan과 `AgentResult`는 기존 중앙 Secret Redaction을 통과한 Canonical JSON만 저장하고 Provider Prompt와 Tool 원문은 저장하지 않는다.
- `DependencyExecutionEngine`은 완료된 Dependency만 원래 Plan 순서로 준비하고 주입된 최대 동시 Step 수로 실행한다. Dependency Result는 선언된 순서로 Executor Port에 전달하며 Timeout·예외·잘못된 Result는 안정적인 실패 Result로 바꾼다.
- Direct는 Subagent Executor 호출 없이 `composing`으로 이동한다. Required 실패는 대기 Step을 취소하고 Run을 `failed`로 종료하며 Optional 실패만 있으면 Warning과 함께 `composing`으로 이동한다.
- 모든 Step·Run 쓰기는 현재 Worker와 유효 Lease를 확인한다. 완료 Result는 복구 뒤 재사용하고 중단된 Idempotent Step만 다음 Attempt를 만들며 취소된 Run의 대기·실행 Step도 같은 Transaction에서 종료한다.
- 실제 Subagent·MCP Tool Loop, Reducer·Output Guardrail, Skill 실행과 Root→Queue Runtime 최종 조립은 WBS-08.4~08.5와 WBS-09~11에 남긴다.

## 4차 구현 결과

- `DeterministicResultReducer`가 `PreparedExecutionPlan`과 `ExecutionOutcome`의 정합성을 먼저 검사하고 입력 Result Tuple과 관계없이 최초 Plan 순서로 본문, Warning과 Evidence를 구성한다. 실패 Run, Mode 불일치, 중복·알 수 없는 Result와 Required Result 누락은 합성 전에 원문 없는 안정적인 오류로 거부한다.
- Deterministic 합성은 성공·Partial Result Summary만 Plan 순서로 배치한다. Partial과 Optional 실패는 Task ID와 안정적인 Error Code가 붙은 Warning으로 남기며 새로운 Fact를 만들거나 Evidence Excerpt를 본문으로 확장하지 않는다.
- Evidence는 Plan과 Result 내부 순서를 유지한다. CRLF·NFC와 Padding을 정규화한 URI가 중복되면 첫 Evidence만 남기고 URI가 없는 Evidence는 독립 Source로 보존한다.
- `synthesis_subagent`는 검증된 DAG의 Terminal Synthesis Result 하나만 최종 본문으로 사용한다. 선행 Summary를 다시 이어 붙이거나 Reducer에서 Model·Subagent를 추가 호출하지 않으며 전체 Plan의 Evidence와 Warning은 보존한다.
- `OrchestrationOutputComposer`가 Direct Answer와 Delegate Reducer 결과를 같은 `OutputCandidate`로 만든 뒤 기존 WBS-06.4.1 Output Guardrail에 전달한다. 호출자에게는 허용된 `SafeOutput`만 반환하고 차단·실패 오류에는 Candidate 원문을 넣지 않는다.
- 최대 Task 수 안에서 Result 순열을 전부 바꾸는 Test로 Markdown, Evidence 순서와 `SafeOutput` Content Fingerprint의 결정성을 고정했다. 영속 Execution Engine과 Synthesis를 함께 실행하는 Test는 계획된 세 Task만 호출되고 합성 시 추가 Executor 호출이 없음을 확인한다.
- `composing → completed|failed` 영속화, Output Event, Run 생성 API와 Queue Handler·Provider Runtime 최종 조립은 WBS-08.5.1~08.5.3에 남긴다.

## 5차 구현 결과

- 승인된 WBS 변경에 따라 기존 WBS-08.5를 WBS-08.5.1~08.5.3으로 분리했다. 이번 단계는 Orchestration Application 생명주기와 안전한 완료 영속화만 소유하며 실제 Provider·Catalog와 HTTP·ASGI 조립은 후속 단계로 유지한다.
- `OrchestrationSubmissionService`는 Guardrail을 통과해 생성된 Run을 `planning`으로 옮긴 뒤 Root Decision을 한 번 수행한다. 성공한 Direct·Delegate Plan만 영속 Queue에 전달하고 Decision·Validation 실패는 외부 실행 전에 `failed`로 종료한다.
- 명시 Skill Decision은 Logical Call 0회를 Event로 기록하고 WBS-11 Skill Runtime이 아직 없으므로 `skill_runtime_unavailable`로 실패 폐쇄한다. 이미 `planning`인 Idempotency Replay는 Root를 다시 호출하지 않고 `orchestration_planning_interrupted`로 종료한다.
- Migration 8은 Run당 하나의 불변 `run_outputs`를 추가한다. Output Guardrail을 통과한 Markdown, Evidence Link, Content Fingerprint와 Guardrail Metadata만 저장하며 Candidate 원문과 Provider Prompt는 저장하지 않는다.
- `OrchestrationRunHandler`는 영속 Plan 실행 결과가 `composing`일 때만 Reducer와 Output Guardrail을 호출한다. `SafeOutput` 저장, `output.completed`, 선택적인 `output.redacted`, `run.completed` Event와 Run 완료 전이는 하나의 Transaction에서 처리한다.
- 실행 Engine이 `composing`으로 전이한 뒤에도 현재 Worker Lease를 유지한다. Heartbeat는 합성이 끝날 때까지 Lease를 갱신하고 Handler 종료나 Lease 만료가 발생하면 Output을 재합성하거나 Root를 재호출하지 않고 `composition_interrupted`로 실패시킨다.
- Direct·Delegate 완료, Decision 실패 뒤 실행 0회, 명시 Skill 0회 호출, 중단된 Planning Replay, Secret 제거, Output Event 순서와 Composing Lease 복구를 Unit·Integration Test로 고정했다.
- 실제 Model Provider 선택과 Root Catalog Adapter는 WBS-08.5.2에서 구현한다. `POST /api/v1/runs`, 서버 측 Data Class 분류, Queue Wake-up과 ASGI 생명주기는 WBS-08.5.3에 남긴다.

## 6차 구현 결과

- 비밀값이 없는 `[model]` 설정에 Root Profile, Provider 최대 요청 횟수, 요청·전체 Timeout과 Retry Backoff를 추가했다. 기존 Schema Version 1 설정은 새 Section이 없어도 기본값으로 읽으며 생성되는 TOML에는 Credential을 넣지 않는다.
- `PolicySelectedModelProvider`는 Model Policy가 승인한 `openai` 또는 `bedrock` Adapter만 지연 생성하고 Provider·Region별로 재사용한다. 선택 의존성 누락, 미지원 Provider와 Bedrock Region 누락은 원문 없는 안정적인 실패로 변환하며 다른 Provider로 Fallback하지 않는다.
- OpenAI Credential은 SDK의 `OPENAI_API_KEY`, Bedrock Credential은 AWS Credential Chain에서 읽는다. 활성 Profile이 선택한 Model과 Region은 기존 Guarded Request를 그대로 사용하며 Credential을 Config, Invocation과 Event에 저장하지 않는다.
- `EmptyRootCatalogProvider`는 Principal 계약을 확인한 뒤 Version이 고정된 빈 불변 Snapshot을 반환한다. WBS-09~11의 Registry가 구현되기 전까지 Direct Decision만 허용하고 존재하지 않는 Subagent·Skill·Connection을 만들지 않는다.
- Root Composition Factory는 SQLite 활성 Model Policy·Invocation 저장소, 중앙 Redaction, JSON Schema Validator, 선택 Provider와 빈 Catalog를 `RootOrchestratorService`에 연결한다. Queue와 ASGI 생명주기는 시작하지 않아 WBS-08.5.3이 같은 Factory를 사용한다.
- 활성 OpenAI Policy의 실제 Root Direct 경로, Policy 누락 시 Provider 생성 0회, Bedrock Region 전달, 선택 의존성·미지원 Provider 실패, Fallback 금지, Config 호환성과 빈 Catalog를 Unit·Integration·Smoke Test로 고정했다.
- Model Policy 생성·초기 활성화 운영 경로는 자동화하지 않는다. 활성 Policy가 없으면 기존 Egress Policy 규칙대로 실패 폐쇄하며 Eval 실행과 활성화 운영 경로는 WBS-15 이후 범위로 유지한다.

## 미결정 사항

- Direct/Delegate 경계의 초기 Prompt 예시
- Synthesis가 필요한 모순/비교 요청의 구체적인 판정 기준
- 기존 공개 Runtime 설정의 Subagent·Run Timeout 상한을 Orchestrator Hard Max와 일치시키는 호환·Migration 방식
