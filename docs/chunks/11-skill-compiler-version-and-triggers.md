# WBS-11 Skill Compiler·Version·Trigger

## 요약

반복 업무를 입력/출력 Schema, 선언형 DAG, 권한, Prompt와 Eval을 가진 Skill로 정의하고 Compiler, 불변 Version, 활성화와 Trigger Registry를 구현한다.

## 목표

- 임의 Python 실행 없이 제한된 Node Type만 허용한다.
- Runtime과 Dashboard가 같은 Canonical `CompiledWorkflow`를 사용하게 한다.
- Eval을 통과한 불변 Version 하나만 Active로 유지한다.
- Command/Alias는 Root 0회, Keyword 후보는 Root 1회 규칙을 보장한다.

## 선행 작업

- WBS-03
- WBS-06
- WBS-09

## 설계서 참조

- [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md): Section 4.4, 11.1~11.6, 11.8, 14.3, 17.2, 23.1~23.3, 24 Phase 4

## 범위

- Skill Manifest/File Layout와 제한 Node Type
- Compiler 검증과 Canonical Workflow JSON
- Draft/Evaluating/Ready/Active/Retired/Rejected Lifecycle
- Command/Alias/Keyword 정규화와 충돌 Registry
- Nested Skill Version Range와 최대 깊이
- Tool/Prompt/Eval Fingerprint와 `needs_review`

## 범위 밖

- Workflow Canvas와 Prompt Viewer UI
- Built-in 업무 Skill의 실제 Workflow
- 자동 Skill 학습/Proposal/Curator
- Arbitrary Python/Shell Node

## 기술 설계

- Skill은 Manifest, Prompt, Eval Resource를 함께 가지며 Secret 값 대신 Reference만 허용한다.
- Node는 input/plain/condition/mcp_tool/subagent/llm/skill/join/output_channel로 제한한다.
- Compiler는 Schema, Unique ID, DAG, Reachability, Node/Depth Limit, Tool Argument, Permission, Input/Output와 Resource Fingerprint를 검사한다.
- Compiler 출력은 Stable Ordering을 가진 Canonical JSON이며 Runtime 실행 입력과 UI Graph의 단일 원본이다.
- 게시 Version은 불변이고 수정은 새 Draft Version을 만든다.
- WBS-03 Unit of Work 위에서 `skills`, `skill_versions`의 Migration, 불변/활성 Unique 제약과 Repository를 이 WBS가 소유한다.
- Active Version은 Skill당 하나이며 Tool/Prompt/Policy Fingerprint 변경 시 `needs_review`로 실행을 차단한다.
- Command/Alias는 전역 Unique, Keyword는 후보 검색 Hint일 뿐 실행 권한이 아니다.

## 구현 체크리스트

- [ ] Skill Manifest Schema와 Resource Loader를 구현한다.
- [ ] 제한 Node Type과 Config Schema를 정의한다.
- [ ] DAG/Reachability/Limit/Version Range Compiler를 구현한다.
- [ ] Tool Schema, Argument Template, Permission과 Prompt Fingerprint 검증을 구현한다.
- [ ] Canonical `CompiledWorkflow` Serializer를 구현한다.
- [ ] Version Lifecycle, Active Unique와 Rollback Transaction을 구현한다.
- [ ] Command/Alias/Keyword 정규화와 Trigger Registry를 구현한다.
- [ ] 명시 Trigger의 Root 0회 실행 경로를 연결한다.
- [ ] Compile/Eval/Activate/Rollback API를 구현한다.

## 검증 체크리스트

- [ ] Cycle, Missing Dependency, Unreachable Node와 초과 Limit을 거부한다.
- [ ] Tool Schema/Prompt/Eval Resource Drift가 실행을 차단하는지 확인한다.
- [ ] 동일 Manifest가 동일 Canonical JSON/Fingerprint를 만드는지 확인한다.
- [ ] Skill당 Active Version이 하나만 존재하는지 동시성 테스트를 실행한다.
- [ ] Command/Alias 충돌과 Unicode 정규화를 테스트한다.
- [ ] Command/Alias Root 0회, Keyword 후보 Root 1회를 불변식으로 검증한다.
- [ ] Built-in과 사용자 Namespace 충돌을 차단한다.

## 완료 조건

- 유효한 Skill을 Compile하고 불변 Version으로 저장할 수 있다.
- Runtime과 UI가 같은 Canonical Workflow JSON을 사용한다.
- Eval/Schema Drift가 있는 Version은 Active 또는 실행될 수 없다.
- Trigger별 Root 호출 수와 전역 충돌 규칙이 지켜진다.

## 미결정 사항

- Manifest Editor에서 지원할 초기 JSON Schema Subset
- Version Range Resolver의 상세 오류 UX
- 사용자 Skill Source-controlled Path의 Watch/Reload 정책
