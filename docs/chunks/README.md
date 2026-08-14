# Pangi 1.0 구현 WBS

이 디렉터리는 [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md)를 실제 구현 순서로 분해한 Work Breakdown Structure다. 루트 문서는 전체 Work Package의 범위, 선행 관계와 상태를 관리하고, 각 청크 문서는 해당 Work Package의 Tech Spec과 체크리스트를 관리한다.

## 운영 규칙

- WBS 번호는 추적 식별자다. 작업을 분리할 때는 `10.1`, `10.2`처럼 하위 번호를 사용하고 기존 번호의 의미를 바꾸지 않는다.
- 상태는 `예정`, `진행 중`, `완료`, `보류`만 사용한다.
- 상태는 문서 작성 상태가 아니라 제품 구현 상태를 뜻한다.
- 현재 20개 Work Package는 초기 구현 기준선이지 고정값이나 상한이 아니다.
- 다음 구현 대상만 상세 설계를 갱신한다. 전체 설계를 미리 확정된 구현 명세처럼 취급하지 않는다.
- Work Package는 여러 PR로 구현할 수 있다. 각 PR은 관련 테스트를 포함하고 병합 후 실행 가능한 상태를 유지한다.
- 설계 결정이 바뀌면 기준 설계서와 영향을 받는 청크를 같은 변경에서 갱신한다.
- `완료`는 청크 문서의 구현·검증 체크리스트와 완료 조건을 모두 충족한 상태다.

## WBS 확장과 변경 승인

- 구현 중 독립적인 결과, 새로운 위험 경계, 별도 검증 Gate, 소유권 또는 선행 관계가 발견되면 Work Package 수를 20개보다 늘린다.
- 숫자를 유지하기 위해 서로 무관한 작업을 기존 Work Package에 억지로 합치지 않는다.
- 기존 Work Package의 목표 안에서 실행 단위만 나눌 때는 `10.1`, `10.2` 같은 하위 번호를 사용한다.
- 새로운 최상위 책임이 생기면 `21`, `22`처럼 다음 정수 번호를 추가한다.
- 기존 Work Package의 목표, 범위/범위 밖, 선행 관계, DoD 담당, Public 계약, 보안 경계 또는 Release 범위를 구체적으로 바꿔야 한다면 임의로 수정하지 않는다.
- 위와 같은 구조 변경 전에는 변경 이유, 제안하는 분해안, 영향을 받는 WBS/DoD, 구현·검증 영향을 정리해 사용자 또는 제품 책임자에게 변경 요청을 올리고 승인을 받은 뒤 반영한다.
- 오탈자, 링크, 구현 상태와 이미 확정된 사실의 동기화처럼 WBS 의미를 바꾸지 않는 수정은 별도 구조 변경 승인을 요구하지 않는다.
- 승인된 확장이나 변경은 Work Package 목록, 기존 Phase 대응, DoD 추적표와 영향받는 Tech Spec을 같은 변경에서 함께 갱신한다.

## Work Package 목록

| WBS | Work Package | 요약 | 선행 작업 | 상태 |
| --- | --- | --- | --- | --- |
| 01 | [Foundation과 Package 경계](01-foundation-and-package-boundaries.md) | 저장소 Skeleton, Dependency 방향과 Public API 경계를 고정한다. | 없음 | 완료 |
| 02 | [설정·Runtime Data·CLI](02-configuration-runtime-data-and-cli.md) | 설정, 데이터 경로, `init/start/status/doctor`의 첫 실행 경로를 만든다. | 01 | 진행 중 |
| 03 | [SQLite 영속성과 Migration](03-sqlite-persistence-and-migrations.md) | 단일 Process SQLite Profile, Unit of Work와 Migration 기반을 만든다. | 01, 02 | 진행 중 |
| 04 | [Web/API Shell과 인증](04-web-api-shell-and-auth.md) | FastAPI, Admin SPA Shell, Bootstrap 인증과 공통 API 계약을 만든다. | 01, 02, 03 | 예정 |
| 05 | [Run 상태·Queue·Event](05-run-state-queue-and-events.md) | Run 계약, 영속 Queue, Lease, Event와 복구 흐름을 만든다. | 03, 04 | 예정 |
| 06 | [Guardrail·보안·Audit](06-guardrails-security-and-audit.md) | 입력·Tool·출력 경계와 Audit 정책을 코드로 강제한다. | 02, 03, 05 | 예정 |
| 07 | [Model Routing과 Egress Policy](07-model-routing-and-egress-policy.md) | Provider Adapter와 데이터 분류 기반 모델 반출 정책을 만든다. | 02, 03, 06 | 예정 |
| 08 | [Root Orchestrator와 실행 Engine](08-root-orchestrator-and-execution-engine.md) | Root 1회 Decision, Plan 검증, 실행과 결정적 결과 합성을 만든다. | 05, 06, 07 | 예정 |
| 09 | [MCP 연결과 Tool Policy](09-mcp-connections-and-tool-policy.md) | MCP Transport, OAuth, Secret, Discovery, Tool 실행과 연결 UI를 만든다. | 03, 04, 06 | 예정 |
| 10 | [Subagent와 Web Search](10-subagents-and-web-search.md) | 깊이 1 Subagent, 제한 병렬 실행, Synthesis와 안전한 Web 검색을 만든다. | 07, 08, 09 | 예정 |
| 11 | [Skill Compiler·Version·Trigger](11-skill-compiler-version-and-triggers.md) | 선언형 Skill, Compiler, 불변 Version과 Trigger Registry를 만든다. | 03, 06, 09 | 예정 |
| 12 | [Workflow UI와 Skill Lifecycle](12-workflow-ui-and-skill-lifecycle.md) | Canonical Graph 기반 Definition/Trace UI와 Skill 관리 UX를 만든다. | 04, 05, 11 | 예정 |
| 13 | [Memory](13-memory.md) | 수동 승인 Memory, Scope 선택, Revision과 주입 Budget을 만든다. | 03, 04, 06, 11 | 예정 |
| 14 | [Scheduler](14-scheduler.md) | `request|skill` Target, Claim/Misfire, 공휴일과 Calendar UI를 만든다. | 05, 08, 11, 13 | 예정 |
| 15 | [Eval과 Red Team](15-eval-and-red-team.md) | Trace Grader, Critical Gate와 승인형 공격 Case 생성을 만든다. | 05~14 | 예정 |
| 16 | [Slack과 Inbound Delivery](16-slack-and-inbound-delivery.md) | Slack/API 요청 수신, Queue 연결, 진행 상태와 안전한 응답 전달을 만든다. | 04~11, 14 | 예정 |
| 17 | [Analytics·관측성·Feedback](17-analytics-observability-and-feedback.md) | Metric/Log/Trace, 조직 채택 지표와 Feedback→Eval 흐름을 만든다. | 05, 11, 14~16 | 예정 |
| 18 | [Admin API Key와 IP Policy](18-admin-api-keys-and-ip-policy.md) | API Key 수명주기, CIDR Allowlist, Lockout 방지와 관리 화면을 만든다. | 03, 04, 06, 17 | 예정 |
| 19 | [Package·운영·Upgrade](19-packaging-operations-and-upgrade.md) | Wheel/UI 배포, Service, Backup, Upgrade/Rollback과 Release CI를 만든다. | 01~18 | 예정 |
| 20 | [Capability Pack과 AB180 Parity](20-capability-packs-and-parity.md) | 8개 공개 사례와 격리 Software Delivery Pack을 Release Gate로 완성한다. | 09~19 | 예정 |

## 기존 Phase 대응

| 설계서 Phase | WBS | 분해 이유 |
| --- | --- | --- |
| Phase 0. Skeleton과 계약 | 01~04 | Package, 실행 환경, Storage와 Web Shell을 각각 검증 가능한 기반으로 분리했다. |
| Phase 1. 한 번 호출하는 Runtime | 05~08 | Run 영속성, 보안, 모델 정책과 Orchestration을 서로 다른 불변식으로 분리했다. |
| Phase 2. MCP와 연결 UI | 09 | 연결 Lifecycle과 Tool 실행 경계가 하나의 통합 계약을 이룬다. |
| Phase 3. Subagent와 Agentic Retrieval | 10 | 제한 실행과 Web Search의 비신뢰 데이터 경계를 함께 검증한다. |
| Phase 4. Skill과 Workflow UI | 11~13 | Skill 실행 계약, 시각화 Lifecycle, Memory 적용 규칙을 분리했다. |
| Phase 5. Scheduler | 14 | 두 Target과 시간·권한·중복 방지 계약을 하나의 Work Package로 유지한다. |
| Phase 6. Eval과 Red Team | 15 | 모든 이전 계약을 Merge/Activation Gate로 연결한다. |
| Phase 7. 운영과 배포 | 16~19 | Channel, 관측/개선, 관리 보안, 배포 Lifecycle로 분리했다. |
| Phase 8. 공개 사례 Capability Pack | 20 | Core 밖의 선택 설치 경계와 8개 사례 Release Gate를 함께 검증한다. |

## Definition of Done 추적

아래 ID는 기준 설계서 Section 27의 체크박스를 위에서부터 순서대로 부여한 추적 ID다. 상세 완료 조건은 연결된 청크 문서와 기준 설계서를 함께 따른다.

| DoD | 요약 | 담당 WBS |
| --- | --- | --- |
| DOD-01~06 | 설치, `init/start/doctor`, Runtime Data와 Gitignore | 02, 19 |
| DOD-07 | Slack Mention 처리 | 16 |
| DOD-08~11 | Root 호출 수, Skill 호출 수, Schedule 호출 수, Subagent 깊이 | 08, 10, 11, 14 |
| DOD-12~17 | MCP Transport, Catalog, OAuth Scope, Tool 기본 Deny, Connection UI | 09 |
| DOD-18~20 | Workflow/Trace, Prompt Viewer, Skill 삭제·복구 | 12 |
| DOD-21~24 | Schedule 화면, Target 구분, 중복 방지, 공휴일 Skip | 14 |
| DOD-25~28 | Eval/Red Team, Web Search, Model Egress | 07, 10, 15 |
| DOD-29~31 | Memory Scope/Revision과 Skill Trigger 호출 규칙 | 11, 13 |
| DOD-32~34 | Adoption Analytics, Cohort, Feedback 승격 | 17 |
| DOD-35~38 | 공개 사례, Software Delivery, Ticket Idempotency, 비용 보고서 | 20 |
| DOD-39~41 | API Key, IP 승인, Chain-of-Thought 비노출 | 06, 18 |
| DOD-42~44 | Backup/Upgrade/Rollback, Uninstall 보존, 안전한 Purge | 19 |
| DOD-45 | Legacy DB 없는 Greenfield 실행 | 01, 03 |

## 변경 절차

1. 구현할 Work Package를 `진행 중`으로 바꾼다.
2. 청크의 미결정 사항을 해소하고 기술 설계와 체크리스트를 현재 코드에 맞춘다.
3. 구현 PR마다 해당 체크리스트와 검증 결과를 갱신한다.
4. 완료 조건과 연결된 DoD를 모두 검증한 뒤 `완료`로 바꾼다.
5. 새 의존성이나 범위를 발견하면 루트 WBS와 영향받는 청크를 함께 수정한다.
