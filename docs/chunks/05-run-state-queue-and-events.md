# WBS-05 Run 상태·Queue·Event

## 요약

모든 Channel과 Scheduler 요청을 하나의 `RunRequest`로 수렴시키고, Run/Step/Event 상태, 영속 Queue, Lease와 중단 복구를 구현한다.

## 목표

- 요청의 수신부터 완료·실패·취소까지 상태 전이를 코드와 DB가 함께 강제한다.
- 외부 Queue 없이 SQLite의 `queued` Run을 중복 없이 Claim한다.
- 실행 행동을 Event로 남기되 Chain-of-Thought와 외부 원문은 저장하지 않는다.
- Restart 뒤 Idempotent 작업만 안전하게 복구한다.

## 선행 작업

- WBS-03
- WBS-04

## 설계서 참조

- [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md): Section 7.3~7.6, 8.1, 8.4, 14.3~14.4, 17.4, 22.3, 23

## 범위

- `Principal`, `RunRequest`, Run/Step/Event Domain Model
- Run 상태 Machine과 실패/Partial Result 계약
- SQLite 영속 Queue, Worker Claim, Lease/Heartbeat와 Semaphore
- Startup Recovery, Cancel과 Idempotent Retry 기준
- Run 생성 Idempotency와 검색 Cursor
- Resource Owner 검사, Event Index, Visibility와 SSE 조회

## 범위 밖

- Root Decision과 실제 Subagent/Tool 실행
- Scheduler의 시간 계산과 Slack 변환
- OpenTelemetry Exporter
- 외부 분산 Queue와 다중 Replica

## 기술 설계

- `runs.state=queued`를 Queue로 사용하고 요청 저장 Transaction이 첫 Event를 함께 기록한다.
- WBS-03 Unit of Work 위에서 `runs`, `run_steps`, `run_events`의 Migration, 제약과 Repository를 이 WBS가 소유한다.
- `api_idempotency_records`를 같은 Unit of Work에 두고 `principal_id + route_key + idempotency_key`로 Run 생성 Replay를 판별한다. 인증·Bootstrap Lifecycle API에는 적용하지 않는다.
- Run 검색은 Stable Sort Key를 포함한 불투명 Cursor를 사용하고 Run 상세·취소·Event 조회는 역할 검사 뒤 Owner 조건을 다시 검사한다.
- Worker는 `BEGIN IMMEDIATE`에서 가장 오래된 Queue Row를 `running`으로 Claim하고 Worker ID, Lease, Heartbeat를 저장한다.
- Run 상태 전이는 Domain Policy가 허용한 Edge만 사용하고 Repository Update에 Expected Revision을 포함한다.
- Required Step 실패는 Run 실패, Optional Step 실패는 Warning을 가진 Partial Result로 구분한다.
- 중단된 Non-idempotent Step은 자동 재실행하지 않고 실패로 종료한다.
- Event는 `run_id + index`로 순서를 보장하고 Public/Admin/Internal Visibility를 적용한다.
- Event Attribute는 구조화 Summary와 Fingerprint만 저장하고 Chain-of-Thought, Prompt와 Tool Result 원문을 금지한다.

## 구현 체크리스트

- [ ] Principal, RunRequest, Run, RunStep과 RunEvent 계약을 정의한다.
- [ ] Run/Step State Machine과 오류 코드를 구현한다.
- [ ] Run 생성과 첫 Event의 원자적 저장을 구현한다.
- [ ] `api_idempotency_records`와 Run 생성 Idempotency를 같은 Transaction에 연결한다.
- [ ] Run 검색의 Stable Cursor 계약을 구현한다.
- [ ] Queue 조회, `BEGIN IMMEDIATE` Claim과 동시 실행 Semaphore를 구현한다.
- [ ] Lease, Heartbeat, Cancel과 Startup Recovery를 구현한다.
- [ ] Idempotent/Non-idempotent 복구 정책을 연결한다.
- [ ] Event Store와 Visibility Filter를 구현한다.
- [ ] Run 상세·취소·Event 조회의 Resource Owner 조건을 구현한다.
- [ ] Run 상세/검색/취소/SSE API를 구현한다.
- [ ] 오래된 `running`과 `queued` Run에 대한 운영 Metric을 연결한다.

## 검증 체크리스트

- [ ] 모든 허용/금지 상태 전이를 Unit Test로 고정한다.
- [ ] 동시 Worker Claim에서 같은 Run이 한 번만 실행되는지 확인한다.
- [ ] Process 중단 뒤 Queue와 Lease Recovery를 Integration Test로 확인한다.
- [ ] Non-idempotent Step이 자동 재실행되지 않는지 확인한다.
- [ ] 같은 Idempotency Key의 Run이 중복 생성되지 않는지 확인한다.
- [ ] Cursor 재사용에서 Run이 중복되거나 누락되지 않는지 확인한다.
- [ ] 다른 사용자의 Run 상세·취소·Event 조회를 거부하는지 확인한다.
- [ ] Event Index가 중복되거나 역전되지 않는지 확인한다.
- [ ] SSE 재연결에서 Last Event 이후 항목만 전달되는지 확인한다.
- [ ] Event와 API에 Chain-of-Thought/원문 Prompt/Secret이 없는지 검사한다.

## 완료 조건

- 요청 한 건이 중복 없이 Claim되고 유효한 상태 전이만 거친다.
- Restart 뒤 Queue는 복구되며 같은 Idempotency Key의 Run이 중복 생성되지 않는다.
- Run Timeline과 첫 실패 Event를 API/SSE에서 확인할 수 있다.
- 비공개 추론과 외부 원문을 저장하거나 노출하지 않는다.

## 미결정 사항

- 첫 운영 Baseline 이후 확정할 Lease/Heartbeat 기본값
- 대기 Queue의 Backpressure와 사용자 안내 임계값
- 장기 실행 Run의 별도 Worker Profile 도입 시점
