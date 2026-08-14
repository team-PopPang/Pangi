# WBS-17 Analytics·관측성·Feedback

## 요약

Run/Step/Model/Tool/Schedule의 운영 상태를 Metric·Log·Trace로 관찰하고, 조직 채택 지표와 사용자 Feedback을 개인정보 경계 안에서 계산해 재현 가능한 Eval 개선으로 연결한다.

## 목표

- 장애 지표와 제품 채택 지표를 분리해 같은 Overview에서 제공한다.
- DAU/WAU/MAU, Eligible 대비 Adoption, Stickiness와 90일 실행을 정의대로 계산한다.
- 작은 Cohort와 개인 활동의 과도한 노출을 막는다.
- Feedback 원문을 자동 학습하지 않고 Synthetic Eval Case Draft로 승격한다.

## 선행 작업

- WBS-05
- WBS-11
- WBS-14~16

## 설계서 참조

- [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md): Section 16.7~16.8, 17.7, 22, 23.1~23.4, 24 Phase 7

## 범위

- Runtime Metric, JSON Log, SQLite Trace와 선택형 OpenTelemetry
- Live/Ready 상세 상태와 운영 Dashboard 기반
- `usage_daily`, Eligible Snapshot, Cohort Version과 Metric Catalog
- DAU/WAU/MAU, Adoption, Stickiness, 90-day Run과 Skill/Schedule 지표
- Overview/Timeseries/Cohort API와 Chart Spec
- Slack/Dashboard Feedback과 Synthetic Eval 승격 Workflow

## 범위 밖

- 개인 성과 평가용 Dashboard
- Prompt/Tool Result를 읽는 Analytics
- Cohort의 Prompt 기반 자동 추론
- Feedback 원문 자동 학습/Prompt 주입

## 기술 설계

- Metric은 Run Trigger/State/Mode, 호출 수/지연, Queue, Schedule, Eval, Guardrail과 Policy 결정을 Stable Label로 기록한다.
- Log는 Request/Run/Step ID와 Error Code만 구조화하고 중앙 Redaction을 통과한다.
- SQLite Event가 기본 Trace이며 OpenTelemetry는 선택 Extra다.
- Aggregate Job은 Instance 현지 날짜, 동일 Idempotency 중복 제거와 Eval/System 제외 규칙을 사용한다.
- Eligible Population은 날짜/Timezone/Source Version Snapshot으로 고정해 과거 분모를 현재 사용자 수로 바꾸지 않는다.
- Cohort는 명시적 Attribute/Group/Membership으로 Versioning하고 조회 시 최소 집계 크기를 적용한다.
- Feedback은 Run/Fingerprint와 연결하고 Reviewer가 고객/Secret을 제거한 Synthetic Fixture로 승격한다.

## 구현 체크리스트

- [ ] Stable Metric 이름/Label과 Export Port를 구현한다.
- [ ] JSON Log Formatter와 Secret Redaction Filter를 구현한다.
- [ ] SQLite Trace 조회와 선택형 OpenTelemetry Span Adapter를 구현한다.
- [ ] Live/Ready의 Worker/Scheduler/DB/Secret/Provider 검사를 구현한다.
- [ ] Eligible Snapshot과 Daily/오늘 증분 Aggregate Job을 구현한다.
- [ ] DAU/WAU/MAU, Adoption, Stickiness, 90일 Window Query를 구현한다.
- [ ] Versioned Cohort, 제한 Metric Catalog와 최소 집계 Size를 구현한다.
- [ ] Overview/Timeseries/Skill/Connection API와 고정 Chart Spec을 구현한다.
- [ ] Feedback 생성/수정/분류와 Synthetic Eval Draft 승격을 구현한다.
- [ ] Overview, Cohort, Feedback 관리 화면을 구현한다.

## 검증 체크리스트

- [ ] Eval/System/Retry 중복을 Active User와 Run 수에서 제외한다.
- [ ] 0분모, 날짜 경계, 윤년, Timezone 변경과 90일 Window를 Fixture로 고정한다.
- [ ] Eligible Snapshot과 과거 Adoption이 Directory 변화로 바뀌지 않는지 확인한다.
- [ ] 중복 Cohort와 최소 집계 미만 그룹이 UI/API에서 보호되는지 확인한다.
- [ ] Log/Metric/Trace에 원문 Prompt, Tool Result와 Secret이 없는지 검사한다.
- [ ] Feedback Comment가 자동 Prompt/학습으로 들어가지 않는지 확인한다.
- [ ] Feedback→Synthetic Draft→Reviewer 승인→Eval Case E2E를 실행한다.

## 완료 조건

- Dashboard에서 Eligible 대비 DAU/WAU/MAU, Stickiness, 90일 실행과 Skill/Schedule Adoption을 확인한다.
- Cohort Graph가 Version, Timezone과 최소 집계 크기를 지킨다.
- 장애의 Run/Step/첫 실패 Event를 Metric/Log/Trace로 추적할 수 있다.
- Feedback을 안전한 Synthetic Eval Case로 승격할 수 있다.

## 미결정 사항

- Pilot 이후 확정할 Adoption 목표값
- OpenTelemetry Exporter의 최초 공식 Backend
- Raw Event와 익명 Aggregate의 조직별 Retention Profile
