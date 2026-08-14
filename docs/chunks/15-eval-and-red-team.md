# WBS-15 Eval과 Red Team

## 요약

Schema/Behavior/Safety/Output을 Trace에서 결정적으로 평가하고, Critical Gate와 사람 승인을 거친 Red Team Case Generator로 Prompt·Model·Skill·Policy 변경의 안전성을 검증한다.

## 목표

- 문장 일치가 아니라 Route, Tool, 권한, 호출 수와 Evidence를 평가한다.
- Critical/Core Behavior 100%와 Secret/Unknown Tool 0건을 Gate한다.
- 실패에서 `expected`, `actual`, `first_bad_event`를 제공한다.
- 생성형 공격 Case는 Reviewer 승인 전 Regression Corpus에 들어가지 않게 한다.

## 선행 작업

- WBS-05~14

## 설계서 참조

- [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md): Section 13, 17.4, 17.7, 21.6, 23, 24 Phase 6

## 범위

- Eval Suite/Case DSL, Synthetic Fixture와 Stub Runtime
- Schema/Behavior/Safety/Output Trace Grader
- Baseline/Candidate 비교와 Activation Gate
- Red Team Attack Surface와 Hostile Test Mode
- Red Team Case Draft Generator, Review와 Export
- Eval/Comparison/첫 실패 Event UI

## 범위 밖

- 운영 Credential/DB를 사용하는 공격 실행
- Semantic 점수만으로 Critical Gate 판정
- 사람 승인 없는 자동 Skill/Prompt 활성화
- AB180 공개 사례의 실제 업무 Fixture 내용

## 기술 설계

- Eval Runtime은 In-memory/Fake MCP와 Synthetic Credential만 사용하고 운영 상태와 격리한다.
- Case DSL은 Input/Fixture와 Decision, Subagent, Required/Forbidden Tool, Argument, Scope, Call Budget, Output/Evidence 기대를 선언한다.
- Grader는 Immutable Run Trace를 읽고 첫 불변식 위반 Event를 결정적으로 계산한다.
- Semantic Grader는 참고 점수이며 모든 Critical Assertion은 결정적이다.
- Prompt/Model/Skill/Tool Schema/Policy/Guardrail/Reducer/Renderer 변경은 영향 Suite를 실행해야 활성화할 수 있다.
- Generator는 안전한 Fingerprint/Summary/Schema만 받아 `RedTeamCaseDraft`를 만들고 Reviewer 승인 뒤 Versioned YAML로 승격한다.
- WBS-03 Unit of Work 위에서 `eval_suites`, `eval_cases`, `eval_runs`, `eval_results`, `red_team_case_drafts`의 Migration, 불변 제약과 Repository를 이 WBS가 소유한다.
- Candidate 실패 기록에는 Redacted Trace만 남기고 실제 Secret/원문 데이터를 금지한다.

## 구현 체크리스트

- [ ] Eval Suite/Case/Run/Result Domain과 YAML DSL Loader를 구현한다.
- [ ] Synthetic Fixture, MCP Stub와 격리 Eval Runtime을 구현한다.
- [ ] Schema/Behavior/Safety/Output Assertion과 Trace Grader를 구현한다.
- [ ] Expected/Actual/First Bad Event 오류 계약을 구현한다.
- [ ] Baseline/Candidate Compare와 Critical Activation Gate를 구현한다.
- [ ] Red Team Attack Fixture와 Hostile Test Mode를 구현한다.
- [ ] Red Team Draft Schema, Generator Policy, 중복/Secret 검사와 Review를 구현한다.
- [ ] 승인 Case의 Versioned YAML Export와 Regression 등록을 구현한다.
- [ ] Eval 목록/Run/Compare/Trace UI와 활성화 Button Gate를 구현한다.

## 검증 체크리스트

- [ ] Root 호출 수, Depth, Required/Forbidden Tool과 Scope Assertion을 자체 테스트한다.
- [ ] Secret/Unknown/Denied Tool과 승인 우회가 Critical 실패인지 확인한다.
- [ ] 같은 Trace가 같은 Grader 결과와 첫 실패 Event를 만드는지 확인한다.
- [ ] 운영 Credential/DB 접근이 Eval Runtime에서 불가능한지 확인한다.
- [ ] Draft가 Reviewer 승인 전에 Gate/Corpus에 들어가지 않는지 확인한다.
- [ ] Prompt/Model/Tool/Policy 변경이 영향 Suite 없이 활성화되지 않는지 확인한다.
- [ ] Critical/Core Behavior 100%와 Baseline 비하락 Gate를 E2E로 검증한다.

## 완료 조건

- Critical Behavior/Red Team Case가 100% 통과해야 활성화할 수 있다.
- Secret Leak, Unknown Tool과 Policy 우회가 0건이다.
- 실패한 Case에서 첫 위반 Event와 기대/실제를 확인할 수 있다.
- Red Team Draft는 Reviewer 승인 전 실행 Gate에 들어가지 않는다.

## 미결정 사항

- 선택형 Semantic Grader Provider와 비용 Budget
- Regression Corpus 보존/폐기 정책
- 변경 Fingerprint에서 영향 Suite를 계산하는 초기 규칙
