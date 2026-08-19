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
5. **Run 생성과 Queue Runtime 통합(WBS-08.5)**: Guarded Run 생성 API, Queue Handler, Model Provider Runtime, Decision·Validation Event를 하나의 실행 경로로 조립한다.

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
- Direct는 `direct_answer`만, Delegate는 1~5개의 DAG Task만, Skill은 활성 이름 하나만 허용한다.
- Hint는 권한으로 사용하지 않고 Execution 단계에서 Policy와 현재 Connection을 다시 검사한다.
- Dependency Cycle, Unknown Subagent, 초과 Budget과 부적절한 Synthesis를 Plan Validator가 거부한다.
- Delegate Task는 운영 기본 3개, 절대 최대 5개로 제한한다. Task Timeout은 최대 180초, Run Timeout은 최대 600초이며 실제 Plan은 주입된 운영 제한도 함께 지킨다.
- Run Budget은 DAG의 Task Timeout을 단순 합산하지 않고 가장 긴 Dependency 경로의 합으로 검증한다.
- 같은 시점에 실행 가능한 Task가 여러 개면 최초 Decision의 Task 순서를 Tie-breaker로 사용해 위상 정렬 결과를 결정적으로 만든다.
- `synthesis_subagent`는 최초 DAG에 포함된 단일 `synthesis` Task가 둘 이상의 선행 Task에 의존하고 다른 Task의 선행 조건이 되지 않을 때만 허용한다.
- Root 결과를 받은 뒤 의미를 바꾸기 위한 재호출이나 Semantic Retry를 금지한다.
- Reducer는 Dependency 순서, Evidence URI Dedup, Warning/실패 Source와 Citation을 결정적으로 구성한다.
- 모순 해석이 필요하면 최초 Decision에 포함된 Synthesis Task만 실행한다.
- Direct Answer와 Reducer 결과는 같은 `OutputCandidate`로 변환해 WBS-06.4.1 Output Guardrail을 통과시킨다. Channel Port에는 원문 Candidate가 아니라 허용된 `SafeOutput`만 전달한다.

## 구현 체크리스트

- [x] Decision/Task/AgentResult/Evidence Schema를 정의한다.
- [ ] Root Prompt/Context Builder와 사용자 입력 Data Envelope를 구현한다.
- [ ] Trigger별 Root logical call Budget을 구현한다.
- [x] Mode별 XOR, DAG, Registry, Task/Timeout/Composition Validator를 구현한다.
- [ ] Direct Result 경로와 Delegate 실행 Plan을 구현한다.
- [ ] Dependency 기반 Execution Engine과 Required/Optional 실패 처리를 구현한다.
- [ ] Deterministic Reducer와 Synthesis Task 연결을 구현한다.
- [ ] Decision, Logical Call, Provider Request와 Validation 실패 Event를 기록한다.
- [ ] API의 Run 생성 경로를 Orchestrator Use Case에 연결한다.

## 검증 체크리스트

- [ ] 일반 자연어, Guardrail 차단, 명시 Skill별 Root 호출 수를 검증한다.
- [x] 잘못된 Mode XOR, Cycle, Unknown Subagent와 초과 Task를 거부한다.
- [ ] Decision 실패 뒤 Tool/Subagent 호출이 0건인지 확인한다.
- [ ] Provider Transport Retry가 Logical Call을 늘리지 않는지 확인한다.
- [ ] Optional 실패와 Required 실패의 Run 상태/출력이 다른지 확인한다.
- [ ] 동일 AgentResult 입력이 동일 Reducer 출력을 만드는지 Property Test를 실행한다.
- [ ] Root 재계획과 Delegation Depth 증가를 불변식 테스트로 차단한다.

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

## 미결정 사항

- Direct/Delegate 경계의 초기 Prompt 예시
- Synthesis가 필요한 모순/비교 요청의 구체적인 판정 기준
- 기존 공개 Runtime 설정의 Subagent·Run Timeout 상한을 Orchestrator Hard Max와 일치시키는 호환·Migration 방식
