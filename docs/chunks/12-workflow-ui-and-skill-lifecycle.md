# WBS-12 Workflow UI와 Skill Lifecycle

## 요약

Skill Definition과 실제 Run Trace를 동일 Canonical Graph로 시각화하고 Prompt 조회, Version Diff, 영향 분석 기반 삭제·복구까지 Skill Author/Admin Lifecycle을 완성한다.

## 목표

- Definition과 Run Trace가 서로 다른 Graph로 Drift하지 않게 한다.
- Node 상태, Duration, Tool/Token과 안전한 입출력 Summary를 표시한다.
- Prompt를 Script/Secret 없이 Sanitized Markdown과 읽기 전용 Source로 보여준다.
- 사용자 Skill은 영향 Dry-run 뒤 Soft Delete/Restore하고 Built-in은 삭제하지 못하게 한다.

## 선행 작업

- WBS-04
- WBS-05
- WBS-11

## 설계서 참조

- [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md): Section 11.6~11.8, 16.2~16.4, 17.2, 21.6, 23.1~23.4, 24 Phase 4

## 범위

- Skill 목록/상세/Version/Workflow Route
- React Flow Definition과 Run Trace Overlay
- Node/Edge 상태, Drawer, Zoom/Fit, JSON과 Version Diff
- Prompt Render/Source/Fingerprint/Diff와 권한
- Skill 삭제 영향 분석, Fingerprint 확인, Soft Delete/Restore
- Trigger/Connection/Eval/사용량 Summary

## 범위 밖

- Skill Compiler 내부 로직
- 실제 Eval Runner와 Scheduler 편집 UI
- Prompt의 브라우저 내 직접 수정
- Built-in Skill 삭제

## 기술 설계

- Backend가 Canonical Workflow와 Run Event를 반환하고 Frontend는 화면 전용 Graph를 재생성하지 않는다.
- Definition은 Version DAG, Trace는 같은 Node ID에 State/Duration/Metric/Error를 Overlay한다.
- Node Drawer는 Redacted Input/Output, Prompt Version과 Error Code만 표시하고 비공개 추론을 금지한다.
- Prompt Renderer는 외부 Asset, Script, Event Handler와 위험 Scheme을 제거하고 Source는 Skill Author/Admin만 조회한다.
- 삭제 전 Schedule, Nested Skill, Active Version과 최근 Run 영향을 계산해 `impact_fingerprint`를 반환한다.
- 삭제 요청은 같은 Fingerprint를 제출해야 하고 상태 변경 시 새 Dry-run을 요구한다.
- 사용자 Skill은 Retention 안에서 복구하고 Built-in/Pack Version은 읽기 전용이다.

## 구현 체크리스트

- [ ] Skill 목록/상세/Version/Workflow API와 Query를 구현한다.
- [ ] Canonical Graph Layout, Node/Edge Renderer와 상태 Token을 구현한다.
- [ ] Definition/Run Trace Mode와 Event Overlay를 구현한다.
- [ ] Zoom/Fit, 조건/병렬 Edge, JSON View와 Version Diff를 구현한다.
- [ ] Node Drawer의 Metric/Error/Redacted Summary를 구현한다.
- [ ] Prompt Sanitizer, Render/Source/Fingerprint/Diff API와 권한을 구현한다.
- [ ] 삭제 영향 분석과 Fingerprint 확인 Mutation을 구현한다.
- [ ] Soft Delete/Restore, Built-in 금지와 Trigger Registry 제거를 연결한다.
- [ ] Skill 상세의 Trigger, Connection, Eval, 사용량 Summary를 구현한다.

## 검증 체크리스트

- [ ] Runtime Node/Edge와 UI Graph가 Canonical JSON에서 일치하는지 Snapshot Test를 실행한다.
- [ ] Run Event가 올바른 Node 상태/순서/Error에 Overlay되는지 확인한다.
- [ ] Prompt Viewer가 Script, Secret, 외부 Asset과 위험 Link를 노출하지 않는지 검사한다.
- [ ] 일반 사용자가 Prompt Source를 조회하지 못하는지 확인한다.
- [ ] 활성 Schedule/Nested Reference가 있는 삭제를 차단한다.
- [ ] Impact Fingerprint가 오래된 삭제 요청을 거부하는지 확인한다.
- [ ] Built-in 삭제 금지와 사용자 Skill 복구를 E2E로 확인한다.

## 완료 조건

- Skill Definition과 Run Trace를 같은 Graph에서 확인할 수 있다.
- Sanitized Prompt, Source, Fingerprint와 Version Diff가 권한에 맞게 표시된다.
- 사용자 Skill은 영향 확인 뒤 Soft Delete/Restore되고 Built-in은 삭제되지 않는다.
- Workflow UI에서 Chain-of-Thought와 Secret 노출이 0건이다.

## 미결정 사항

- Graph 20개 초과 시 Mini-map의 상세 UX
- 수동 Node 위치를 Version 간 유지하는 규칙
- 대형 Prompt Diff의 표시/접기 한계
