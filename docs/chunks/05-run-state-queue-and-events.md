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

## 내부 구현 단계

WBS 번호와 문서는 유지하고 아래 실행 단위를 독립 PR로 구현한다.

1. **Run Core 계약과 Schema**: Principal, RunRequest, Run/Step/Event, 상태 전이와 기능 Table Migration을 고정한다.
2. **Run 생성과 조회**: Run·첫 Event 원자 저장, Idempotency, Cursor와 Owner 검사를 구현한다.
3. **영속 Queue와 복구**: Worker Claim, Semaphore, Lease, Heartbeat, Cancel과 Startup Recovery를 구현한다.
4. **Run API와 Event 전달**: 상세·검색·취소 API, SSE 재연결과 Queue 운영 Metric을 연결한다.

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
- `runs.idempotency_key`는 추적용 값이며 전역 Unique로 만들지 않는다. Replay 유일성은 `api_idempotency_records`의 복합 Key만 소유한다.
- `route_key`는 요청 본문이 아니라 신뢰할 수 있는 Inbound Adapter가 전달한다. Request Fingerprint는 Channel, Text, Thread, Explicit Skill, Schedule과 순서가 보존된 Attachment Metadata를 포함하고, 재시도마다 달라지는 Request ID·생성 시각·Idempotency Key는 제외한다.
- Idempotency Record의 기본 TTL은 24시간이다. 같은 복합 Key를 다시 사용할 때 만료된 Record만 Transaction 안에서 제거하고 새 Run을 만든다. TTL 안의 같은 Fingerprint는 기존 Run을 Replay하고 다른 Fingerprint는 안정적인 충돌 오류로 거부한다.
- Restart 뒤 Queue가 실행 입력을 복구할 수 있도록 정규화된 Request Text와 Attachment 참조를 저장한다. Slack Event 원본 JSON, Attachment 본문, Provider Prompt와 Tool Result 원문은 저장하지 않는다.
- Run 검색은 `(created_at DESC, id DESC)` Keyset과 Versioned JSON을 URL-safe Base64로 인코딩한 불투명 Cursor를 사용한다. Cursor는 Actor ID·Role, Effective Owner Scope와 상태·Trigger Filter의 Fingerprint에 묶어 다른 조회 조건에서 재사용할 수 없게 한다.
- Member, Skill Author와 System은 자신의 Run만 조회하고 Admin은 전체 Run을 조회한다. 비활성 사용자와 소유자가 다른 Run의 상세 조회는 존재 여부가 드러나지 않도록 동일한 Not Found로 처리한다.
- 목록은 정규화된 Request Text와 Attachment를 제외한 Metadata Summary만 반환한다. 상세 조회는 Owner/Admin 검사를 통과한 뒤에만 전체 정규화 Request를 복원한다.
- Worker는 `BEGIN IMMEDIATE`에서 가장 오래된 Queue Row를 `running`으로 Claim하고 Worker ID, Lease, Heartbeat를 저장한다.
- Run 상태 전이는 Domain Policy가 허용한 Edge만 사용하고 Repository Update에 Expected Revision을 포함한다.
- Required Step 실패는 Run 실패, Optional Step 실패는 별도 `partial` Run 상태를 만들지 않고 Warning을 가진 `completed` 결과로 구분한다.
- 중단된 Non-idempotent Step은 자동 재실행하지 않고 실패로 종료한다.
- Event는 `run_id + index`로 순서를 보장하고 Public/Admin/Internal Visibility를 적용한다.
- Event Attribute는 구조화 Summary와 Fingerprint만 저장하고 Chain-of-Thought, Prompt와 Tool Result 원문을 금지한다.

## 구현 체크리스트

- [x] Principal, RunRequest, Run, RunStep과 RunEvent 계약을 정의한다.
- [x] Run/Step State Machine과 오류 코드를 구현한다.
- [x] Run Core Table과 Idempotency Record Migration·제약을 구현한다.
- [x] Run 생성과 첫 Event의 원자적 저장을 구현한다.
- [x] `api_idempotency_records`와 Run 생성 Idempotency를 같은 Transaction에 연결한다.
- [x] Run 검색의 Stable Cursor 계약을 구현한다.
- [x] Queue 조회, `BEGIN IMMEDIATE` Claim과 동시 실행 Semaphore를 구현한다.
- [x] Lease, Heartbeat, Cancel과 Startup Recovery를 구현한다.
- [x] Idempotent/Non-idempotent 복구 정책을 연결한다.
- [ ] Event Store와 Visibility Filter를 구현한다.
- [x] Run 목록·상세 조회의 Resource Owner 조건을 구현한다.
- [ ] Run 취소·Event 조회의 Resource Owner 조건을 구현한다.
- [ ] Run 상세/검색/취소/SSE API를 구현한다.
- [ ] 오래된 `running`과 `queued` Run에 대한 운영 Metric을 연결한다.

## 검증 체크리스트

- [x] 모든 허용/금지 상태 전이를 Unit Test로 고정한다.
- [x] 동시 Worker Claim에서 같은 Run이 한 번만 실행되는지 확인한다.
- [x] Process 중단 뒤 Queue와 Lease Recovery를 Integration Test로 확인한다.
- [x] Non-idempotent Step이 자동 재실행되지 않는지 확인한다.
- [x] 같은 Idempotency Key의 Run이 중복 생성되지 않는지 확인한다.
- [x] Cursor 재사용에서 Run이 중복되거나 누락되지 않는지 확인한다.
- [x] 다른 사용자의 Run 목록·상세 조회를 거부하는지 확인한다.
- [ ] 다른 사용자의 Run 취소·Event 조회를 거부하는지 확인한다.
- [ ] Event Index가 중복되거나 역전되지 않는지 확인한다.
- [ ] SSE 재연결에서 Last Event 이후 항목만 전달되는지 확인한다.
- [ ] Event와 API에 Chain-of-Thought/원문 Prompt/Secret이 없는지 검사한다.

## 1차 구현 결과

- Framework 의존성이 없는 불변 `Principal`, `AttachmentRef`, `RunRequest`, `Run`, `RunStep`, `RunEvent` 계약을 추가했다.
- Embedding Client가 사용할 `AttachmentRef`, `Principal`, `RunRequest`, `RunEvent`를 Package Root Public API에 노출했다.
- Run과 Step의 허용 Edge를 명시적 State Machine으로 고정하고 잘못된 전이를 안정적인 오류 코드로 거부한다.
- Required Step 실패는 `failed`, Optional Step 실패는 Warning이 있는 `completed` 결과로 구분한다.
- `0003_run_core.sql`이 `runs`, `run_steps`, `run_events`, `api_idempotency_records`와 Queue·Owner·Event 조회 Index를 추가한다.
- Idempotency Replay는 `principal_id + route_key + idempotency_key` 복합 Key가 소유하고 `runs.idempotency_key`는 전역 Unique로 만들지 않았다.
- Event Attribute는 Chain-of-Thought, Provider Prompt, Slack 원본 Event, Attachment 본문과 Tool Result 원문용 Key를 거부한다.
- 실제 Run 저장 Use Case, Worker와 API/SSE가 남아 있으므로 WBS-05 상태는 `진행 중`으로 유지한다.

## 2차 구현 결과

- Framework 의존성이 없는 Run 생성·상세·목록 계약과 Application Service, SQLite Store를 추가했다.
- Run 생성 시 `received` Run과 공개 `run.received` 첫 Event, 처리 완료 Idempotency Record를 하나의 Transaction으로 저장한다. 중간 실패는 세 Record를 모두 Rollback한다.
- 같은 Principal·Route·Idempotency Key와 같은 Fingerprint는 기존 Run을 반환하고, 다른 Fingerprint는 충돌로 거부한다. 만료 기준은 24시간이며 해당 Key를 다시 사용할 때 만료 Record를 정리한다.
- 목록은 `(created_at DESC, id DESC)` Keyset Cursor를 사용하고 Cursor를 Actor·Owner Scope·Filter에 묶는다. 같은 생성 시각의 Run, 페이지 사이에 추가된 Run, 다른 Scope에서의 재사용과 손상된 Cursor 거부를 Test로 고정했다.
- Member, Skill Author와 System은 자신의 Run만, Admin은 전체 Run을 조회한다. 목록에는 Metadata만 포함하고 Owner/Admin 검사를 통과한 상세 조회에서만 정규화된 Request를 복원한다.
- 영속 Queue·Lease·복구·취소는 3차, Run API·Event 조회·SSE는 4차 구현 단위로 남아 있으므로 WBS-05 상태는 `진행 중`으로 유지한다.

## 3차 구현 결과

- `runs.state=queued`와 기존 Queue Index를 사용하고 `BEGIN IMMEDIATE` Transaction 안에서 `queued_at`, `created_at`, `id` 순서의 Run을 하나만 Claim한다.
- Claim은 Run을 `running`으로 전환하면서 Worker ID, Lease와 Heartbeat를 원자적으로 저장한다. 상태 변경은 Revision CAS를 사용하고 Heartbeat는 유효한 Lease의 현재 Worker만 갱신한다.
- `asyncio.Event` 기반 Wake-up과 `max_concurrent_runs` Semaphore, 활성 Task Registry를 가진 Process-local Queue Runtime을 추가했다.
- 대기·실행 Run을 원자적으로 취소하고 이미 취소된 Run의 재요청은 같은 결과를 반환한다. 취소 뒤 오래된 Worker의 Heartbeat와 상태 쓰기는 거부한다.
- Startup과 Worker 종료에서 중단된 Run을 복구한다. 실행 중이던 Step이 없거나 모두 Idempotent면 재대기하고, Non-idempotent Step이 있으면 `non_idempotent_recovery`로 Run과 중단 Step을 실패시킨다.
- Queue 상태 변경과 `run.queued`, `run.running`, `run.interrupted`, `run.cancelled`, `run.failed` Event를 같은 Transaction에 저장한다.
- Lease Duration과 Heartbeat Interval은 `RunQueuePolicy`로 주입한다. 운영 Baseline 없이 공개 설정 기본값을 고정하지 않았고 Test는 명시적인 시간 정책을 사용한다.
- 실제 Root Orchestrator와 실행 Handler는 WBS-08에서, Owner 기반 취소 API·Event 조회·SSE·Queue Metric은 WBS-05.4에서 연결하므로 WBS-05 상태는 `진행 중`으로 유지한다.

## 완료 조건

- 요청 한 건이 중복 없이 Claim되고 유효한 상태 전이만 거친다.
- Restart 뒤 Queue는 복구되며 같은 Idempotency Key의 Run이 중복 생성되지 않는다.
- Run Timeline과 첫 실패 Event를 API/SSE에서 확인할 수 있다.
- 비공개 추론과 외부 원문을 저장하거나 노출하지 않는다.

## 미결정 사항

- 첫 운영 Baseline 이후 확정할 Lease/Heartbeat 기본값
- 대기 Queue의 Backpressure와 사용자 안내 임계값
- 장기 실행 Run의 별도 Worker Profile 도입 시점
