# WBS-16 Slack과 Inbound Delivery

## 요약

Slack Socket Mode/HTTP Events와 API 요청을 공통 RunRequest/Queue 경로로 변환하고, 빠른 Ack, 진행 상태, Approval/Cancel과 안전한 최종 응답 전달을 구현한다.

## 목표

- Slack Retry와 중복 Event가 같은 Run을 두 번 만들지 않게 한다.
- Channel Adapter가 모델을 직접 호출하거나 Core 정책을 우회하지 않게 한다.
- 긴 Markdown, Mention과 Link를 Slack에 안전하게 변환한다.
- Scheduler 결과와 일반 요청이 같은 Destination/Trace 계약을 사용하게 한다.

## 선행 작업

- WBS-04~11
- WBS-14

## 설계서 참조

- [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md): Section 7.3, 8.1, 9.8, 12.5, 16.6, 17.4, 19.8, 21.2~21.4, 23.2~23.4, 24 Phase 7

## 범위

- Slack OpenID Connect와 Dashboard Identity·Session 연결
- Slack Socket Mode와 선택형 HTTP Events Adapter
- Signature/Timestamp, Workspace/User/Channel Identity와 Idempotency
- Event/Command/Block Action→RunRequest 변환
- 빠른 Ack, Progress Message Update와 Thread 연결
- Markdown-to-Slack Renderer, Chunking, Mention/Link Policy
- Approval, Cancel, Open Dashboard Action
- API Inbound Run과 Scheduler Destination 전달

## 범위 밖

- Slack 전체 History Search Subagent
- Slack App 생성/배포 자동화
- Channel Adapter 안의 모델 호출
- Message 원문 장기 저장

## 기술 설계

- Socket Mode를 기본으로 하고 HTTP Events는 Signature와 허용 Timestamp Window를 검증한다.
- Slack OpenID Connect는 검증된 Workspace/User Subject를 기존 `auth_identities(provider='slack')`와 User·Session 경계에 연결하며 Event 서명 검증과 분리한다.
- `team_id:event_id`를 Idempotency Key, `thread_ts`를 Thread Key로 사용한다.
- Adapter는 인증된 Principal과 정규화된 Attachment를 포함한 RunRequest만 만들고 Queue Commit 뒤 Ack/Progress를 처리한다.
- 하나의 Progress Message를 갱신하고 완료 응답은 별도 Message와 Run Detail Link로 보낸다.
- Renderer는 Markdown 구조를 Slack Block/Text Limit에 맞게 의미 단위로 나누고 `@channel/@here`와 사용자 Mention을 기본 Escape한다.
- Block Action은 서명된 Stable Action ID와 요청자/Run 소유권을 검증한 뒤 Approval/Cancel Use Case만 호출한다.
- Scheduler는 저장된 Destination에 새 Thread를 만들되 실행 시 Channel 권한을 다시 확인한다.

## 구현 체크리스트

- [ ] Slack App Config Schema와 Socket/HTTP Adapter Protocol을 구현한다.
- [ ] Slack OpenID Connect와 기존 User·Session 연결을 구현한다.
- [ ] Signature/Timestamp/Workspace/User/Channel 인증을 구현한다.
- [ ] Mention, Command, Block Action을 RunRequest/Use Case로 변환한다.
- [ ] Event Idempotency와 Queue Commit 뒤 Ack를 구현한다.
- [ ] Thread/Progress Message 상태와 실패 복구를 구현한다.
- [ ] Markdown-to-Slack Renderer, 길이 분할과 Mention/Link Sanitizer를 구현한다.
- [ ] Approval/Cancel/Open Dashboard Action과 소유권 검사를 구현한다.
- [ ] Scheduler/API Destination Adapter와 전달 Event/Metric을 구현한다.
- [ ] Slack 연결 상태를 Doctor/Ready/관리 화면에 연결한다.

## 검증 체크리스트

- [ ] 같은 Event Retry가 Run을 한 번만 만드는지 확인한다.
- [ ] 잘못된 OIDC State·Nonce·Issuer·Audience와 연결되지 않은 Identity를 거부한다.
- [ ] 잘못된 Signature/Timestamp/Workspace 요청을 Ack 전후 안전하게 거부한다.
- [ ] Adapter의 모델/Tool 직접 호출이 Architecture Test에서 실패하는지 확인한다.
- [ ] Thread, Progress Update와 최종 Message 순서를 Contract Test로 고정한다.
- [ ] 긴 Markdown, Code, Table, Link와 Mention Renderer Fixture를 테스트한다.
- [ ] 다른 사용자의 Approval/Cancel Action을 거부한다.
- [ ] Slack Mention→Direct/Delegate→응답과 Scheduler Destination E2E를 실행한다.

## 완료 조건

- Slack OpenID Connect로 연결된 사용자가 기존 Dashboard Session 경계를 사용한다.
- Slack Socket Mode Mention이 공통 Queue를 통해 처리된다.
- Duplicate Event로 중복 Run이 발생하지 않는다.
- 진행 상태와 최종 응답이 같은 Thread에 안전하게 전달된다.
- Channel Adapter가 Root/Tool/Guardrail 정책을 우회하지 않는다.

## 미결정 사항

- HTTP Events의 최초 지원 배포 Profile
- Slack Block/Message별 최대 분할 정책
- Progress Update 빈도 제한의 초기값
