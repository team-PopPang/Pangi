# WBS-14 Scheduler

## 요약

Once/Cron Schedule이 자연어 `request` 또는 고정 Version `skill` 중 하나를 선택해 기존 Queue와 Guardrail로 Run을 만들고, Restart·DST·Misfire·공휴일에도 중복 없이 동작하게 한다.

## 목표

- Target XOR와 Target별 Root 호출 수를 DB/API/UI/Trace에서 일관되게 강제한다.
- 실행 시점의 현재 권한과 Connection 상태를 재검사한다.
- Claim Unique와 Revision으로 중복·과거 설정 실행을 막는다.
- 공휴일 Calendar Version과 영향 Preview를 제공한다.

## 선행 작업

- WBS-05
- WBS-08
- WBS-11
- WBS-13

## 설계서 참조

- [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md): Section 7.1, 12, 14.3, 17.3, 21.6, 23.1~23.5, 24 Phase 5

## 범위

- Once/5-field Cron, IANA Timezone과 DST
- `request|skill` Target, 암호화 자연어 요청과 고정 Skill Version
- Tick, Unique Claim, Misfire/Coalesce/Max Instances와 Recovery
- 불변 Holiday Calendar Version과 `skipped_holiday`
- Schedule/Occurrence/Holiday API와 Calendar/Card/Form UI
- Run Now/Pause/Soft Delete/Revision과 Permission Dry-run

## 범위 밖

- Scheduler 전용 실행 Engine 또는 과거 권한 Snapshot
- 가변 `latest` Skill Version
- 실행 시 원격 ICS Fetch
- 다중 Host Scheduler Claim

## 기술 설계

- Schedule은 `target_type=request|skill` 중 정확히 하나이며 DB Check와 API Discriminated Union으로 강제한다.
- Request Text는 최대 8KB, 생성/실행 Guardrail을 거치며 DB에는 Ciphertext/Key Version/Fingerprint만 저장한다.
- Skill Target은 불변 Version과 Schema 검증 Input을 저장하고 Root 호출 없이 실행한다.
- Tick은 Due Schedule을 읽고 현지 날짜/고정 Holiday Version을 검사한 뒤 `schedule_id+scheduled_for` Unique Row를 Transaction에서 Insert한다.
- WBS-03 Unit of Work 위에서 `holiday_calendars`, `holiday_calendar_versions`, `schedules`, `schedule_runs`의 Migration, XOR/Unique 제약과 Repository를 이 WBS가 소유한다.
- 공휴일은 `skipped_holiday` 한 건을 남기고 Run을 만들지 않으며 다음 시각을 계산한다.
- Misfire `skip|run_once`, Coalesce와 DST 규칙을 결정적으로 적용한다.
- 실행 직전 현재 Role/Connection/Tool Policy를 재검사하고 Schedule 생성 시점 권한을 복제하지 않는다.

## 구현 체크리스트

- [ ] Schedule/Holiday Calendar/Occurrence Domain과 Target XOR를 구현한다.
- [ ] Cron/Once, IANA Timezone, DST와 다음 실행 계산을 구현한다.
- [ ] Request Text 암호화/복호화/Fingerprint와 권한 조회를 구현한다.
- [ ] Tick, Transactional Claim, Unique Occurrence와 Recovery를 구현한다.
- [ ] Misfire, Coalesce, Max Instances와 Run Now/Pause를 구현한다.
- [ ] Holiday Import/Version/Diff/Activation과 영향 Fingerprint를 구현한다.
- [ ] Target을 RunRequest로 정규화하고 현재 권한 재검사를 연결한다.
- [ ] Schedule/Occurrence/Holiday API와 Calendar/Card/Form UI를 구현한다.
- [ ] Target, Revision, Root Call과 Skip Event를 Trace/Audit에 기록한다.

## 검증 체크리스트

- [ ] DB/OpenAPI/UI의 Request/Skill XOR를 Contract Test로 고정한다.
- [ ] 자연어 Schedule Root 1회와 Skill Schedule Root 0회를 검증한다.
- [ ] Restart/동시 Tick에서 같은 Occurrence가 한 번만 Claim되는지 확인한다.
- [ ] DST 중복/누락 시각과 Misfire 경계 Fixture를 테스트한다.
- [ ] 공휴일 Skip Event 1건, Run 0건과 다음 Occurrence를 확인한다.
- [ ] Holiday Version 전환 Preview와 실제 결과가 일치하는지 확인한다.
- [ ] 권한/Connection이 사라진 Owner의 실행을 차단한다.
- [ ] Calendar/Card의 Target, Owner, Cron, Destination과 상태를 E2E로 확인한다.

## 완료 조건

- Restart 뒤 Schedule 중복 실행이 0건이다.
- 자연어 Target은 Root 정확히 1회, Skill Target은 Root 0회다.
- 고정 Holiday Calendar 현지 날짜에서 Run 없이 Skip Event를 한 번 남긴다.
- UI/API/DB/Trace가 같은 Target, Revision과 다음 실행을 표시한다.

## 미결정 사항

- 기본 Tick 간격과 한 Tick의 Claim Batch Size
- 지원할 Holiday Provider Adapter의 첫 목록
- 자연어 Schedule의 조직별 최소/최대 실행 빈도
