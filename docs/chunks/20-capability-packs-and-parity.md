# WBS-20 Capability Pack과 AB180 Parity

## 요약

AB180 공개 사례 8개를 Core에 결합하지 않고 공식 Capability Pack의 선언형 Skill/Subagent/Eval로 제공하며, 고권한 Software Delivery는 별도 Process/Filesystem/Credential 경계에서 실행한다.

## 목표

- 8개 공개 사례의 Critical Benchmark를 Synthetic Fixture로 100% 통과한다.
- 설치되지 않은 Pack이 Core Registry/시작 시간/권한에 영향을 주지 않게 한다.
- 각 Skill의 필요 Connection/권한/출력 계약을 실행 전에 보여준다.
- Ticket 생성과 Repository Push/PR을 사용자 승인·OAuth·Idempotency로 통제한다.

## 선행 작업

- WBS-09~19

## 설계서 참조

- [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md): Section 3.5, 9.10, 10.9, 11.9, 13, 18.3, 18.6, 23.7, 24 Phase 8, 27

## 범위

- `standard`, `google-workspace`, `engineering`, `data`, `software-delivery`, `ab180-parity` Manifest
- `ticket-analysis`, `meeting-coordinator`, `stale-document-finder`, `change-history`
- `cost-insight-report`, `work-digest`, `usage-report`, `ticket-to-pr`
- Pack 호환성/Health/제거 영향 분석과 Dashboard
- `ensure-ticket`, External Mutation Idempotency와 User OAuth
- 격리 Repo Sandbox Worker, Diff/Test/Secret/License Gate와 Draft PR
- 8개 Behavior/Red Team Benchmark와 Release Gate

## 범위 밖

- AB180 비공개 Source/Prompt/DB Schema 복제
- Core Process의 Shell/Repository Credential 소유
- 승인 전 외부 Ticket/Push/PR 생성
- 기본 Branch Push, Force Push, Merge, Release와 배포
- 설치되지 않은 Pack의 Connection Credential 강제

## 기술 설계

- Pack은 Versioned Manifest와 Python Entry Point로 Skill/Subagent/Worker/Eval을 등록하고 Core Registry를 직접 수정하지 않는다.
- Core/Skill API/Required Capability 호환성을 시작 전에 검사하고 Pack 실패는 해당 Pack만 `unhealthy`로 두며 Write 기능을 비활성화한다.
- Built-in Skill은 결정적 계산 Node와 Evidence를 우선하고 LLM은 계산/권한/시간을 새로 추측하지 않는다.
- 연결이 없으면 `connection_required` 상태와 Action을 표시하고 Core 시작은 유지한다.
- Software Delivery는 Ticket 확인→필요 시 승인형 생성→조사→Plan 승인→격리 Worktree Patch/Test→Diff 승인→사용자 GitHub OAuth Push→Draft PR 순서다.
- Ticket Idempotency는 Principal/요청 Fingerprint/Repository로 계산하고 Remote ID/URL을 `external_mutations`에 영속화한다.
- WBS-03 Unit of Work 위에서 `capability_packs`, `external_mutations`의 Migration, Idempotency 제약과 Repository를 이 WBS가 소유한다.
- Sandbox는 등록 Command Template, Resource Limit, 기본 Network 차단과 Run별 Worktree를 사용한다.
- `ab180-parity` Release Gate는 8개 Suite의 Critical Case 100%와 공통 Root/Depth/권한/Evidence/Egress 불변식을 요구한다.

## 구현 체크리스트

- [ ] Capability Pack Manifest Loader, 호환성, Health와 제거 영향 분석을 구현한다.
- [ ] Pack별 Catalog/Skill/Subagent/Eval Entry Point를 구성한다.
- [ ] `ticket-analysis` Workflow와 고객 자동 전송 금지를 구현한다.
- [ ] `meeting-coordinator`의 결정적 Timezone/Interval/Resource 계산을 구현한다.
- [ ] `stale-document-finder`의 수정/참조/대체 문서 Evidence를 구현한다.
- [ ] `change-history`의 UTC 정규화, Correlation과 추정 표시를 구현한다.
- [ ] `cost-insight-report`의 결정적 계산과 고정 Output Schema를 구현한다.
- [ ] `work-digest`의 기간/권한/중복 Correlation과 Scheduler 전달을 구현한다.
- [ ] `usage-report`의 집계/Privacy/고정 Chart Renderer를 구현한다.
- [ ] `ensure-ticket` 승인, User OAuth와 External Mutation Idempotency를 구현한다.
- [ ] Repo Sandbox Worker와 Plan/Diff/Test/Secret/License 승인 Gate를 구현한다.
- [ ] 사용자 OAuth Branch Push와 Draft PR Publisher를 구현한다.
- [ ] 8개 Benchmark/Red Team Suite와 `ab180-parity` Release Gate를 구현한다.
- [ ] Pack/Skill 상세에서 필요 Connection, 권한, Health와 Workflow Trace를 표시한다.

## 검증 체크리스트

- [ ] 설치하지 않은 Pack이 Core Registry/시작 시간에 영향을 주지 않는지 확인한다.
- [ ] Pack 호환 실패가 Core Ready를 내리지 않고 해당 Write 기능만 차단하는지 확인한다.
- [ ] 8개 사례의 Required/Forbidden Tool, Evidence, Output Schema와 권한을 검증한다.
- [ ] 비용 합계 불일치, Calendar 권한, 오래된 문서 오탐과 Digest 기간 경계를 테스트한다.
- [ ] Ticket Retry/Restart가 외부 Ticket을 정확히 한 번만 만드는지 확인한다.
- [ ] Ticket ID가 Branch/Commit/Draft PR에 일관되게 연결되는지 확인한다.
- [ ] 승인 전 Ticket/Push/PR, 기본 Branch/Force Push와 Sandbox 이탈이 0건인지 확인한다.
- [ ] `ab180-parity` 8개 Critical Benchmark가 Stub CI와 실제 Sandbox Contract 환경에서 100% 통과하는지 확인한다.

## 완료 조건

- 8개 공개 사례 Benchmark의 Critical Case가 100% 통과한다.
- 모든 Pack이 요구 Connection/권한/Health와 설치 전 영향을 표시한다.
- Software Delivery는 별도 Worker 경계에서만 실행되며 승인 전 외부 Mutation이 0건이다.
- Ticket 없는 개발 요청은 승인 뒤 Ticket을 정확히 한 번 만들고 Branch/Commit/Draft PR에 연결한다.
- 비용 보고서는 TL;DR, 비교표, 시각화, 조치, 가정·주의사항과 계산 근거를 포함한다.

## 미결정 사항

- Repo Sandbox 격리 기술의 최종 선택
- Pack Signing/Distribution과 호환성 Metadata 형식
- 실제 외부 Sandbox Contract Test를 실행할 CI Environment
