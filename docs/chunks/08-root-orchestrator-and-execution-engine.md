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
- Root 결과를 받은 뒤 의미를 바꾸기 위한 재호출이나 Semantic Retry를 금지한다.
- Reducer는 Dependency 순서, Evidence URI Dedup, Warning/실패 Source와 Citation을 결정적으로 구성한다.
- 모순 해석이 필요하면 최초 Decision에 포함된 Synthesis Task만 실행한다.

## 구현 체크리스트

- [ ] Decision/Task/AgentResult/Evidence Schema를 정의한다.
- [ ] Root Prompt/Context Builder와 사용자 입력 Data Envelope를 구현한다.
- [ ] Trigger별 Root logical call Budget을 구현한다.
- [ ] Mode별 XOR, DAG, Registry, Task/Timeout/Composition Validator를 구현한다.
- [ ] Direct Result 경로와 Delegate 실행 Plan을 구현한다.
- [ ] Dependency 기반 Execution Engine과 Required/Optional 실패 처리를 구현한다.
- [ ] Deterministic Reducer와 Synthesis Task 연결을 구현한다.
- [ ] Decision, Logical Call, Provider Request와 Validation 실패 Event를 기록한다.
- [ ] API의 Run 생성 경로를 Orchestrator Use Case에 연결한다.

## 검증 체크리스트

- [ ] 일반 자연어, Guardrail 차단, 명시 Skill별 Root 호출 수를 검증한다.
- [ ] 잘못된 Mode XOR, Cycle, Unknown Subagent와 초과 Task를 거부한다.
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

## 미결정 사항

- Direct/Delegate 경계의 초기 Prompt 예시
- Synthesis가 필요한 모순/비교 요청의 구체적인 판정 기준
- Run Timeout과 Task 기본 Limit의 첫 운영값
