---
name: pangi-planning-pipeline
description: 팡이 저장소 전용 계획/방향성 검토 워크플로우. 팡이에서 구현 전에 방향을 잡아야 하거나, MVP 범위를 정리해야 하거나, Slack 수신·orchestrator·job worker·worktree·Codex runner·storage·approval flow 같은 설계를 검토해야 할 때 사용한다. `AGENTS.md`, `README.md`, MVP 문서, 체크리스트, 안전 규칙, 관련 아키텍처 문서와 `.codex/prompts/researcher.md`, `planner.md`, `reviewer.md`를 읽고 Researcher -> Planner -> Reviewer 순서로 근거 있는 계획을 정리한다.
---

# Pangi Planning Pipeline

## 목적

팡이 작업을 구현하기 전에 현재 저장소 기준으로 사실, 제약, 구현 순서, 리스크를 빠르게 정리한다.
항상 작은 MVP 범위와 안전 규칙을 먼저 확인하고, 확인한 사실과 추정을 분리해 답한다.

## 기본 컨텍스트

작업을 시작하면 아래 파일을 먼저 읽는다.

1. `AGENTS.md`
2. `README.md`
3. `docs/mvp/overview.md`
4. `docs/implementation-checklist.md`
5. `docs/security/safety-rules.md`
6. `.codex/prompts/researcher.md`
7. `.codex/prompts/planner.md`
8. `.codex/prompts/reviewer.md`

프롬프트 파일이 없거나 읽을 수 없으면 누락 경로를 답변에 적고, 확인 가능한 문서만으로 계획을 정리한다.

## 문서 라우팅

요청 주제에 맞는 세부 문서만 추가로 읽는다.

| 작업 주제 | 추가로 읽을 문서 |
| --- | --- |
| Slack 수신, thread 응답, 이벤트 검증 | `docs/architecture/slack.md` |
| 요청 분류, job type, prompt 정책 | `docs/architecture/orchestrator.md` |
| background queue, 상태 전이, 동시 실행 | `docs/architecture/jobs.md` |
| worktree 생성, branch 규칙, diff 안전 확인 | `docs/architecture/git-worktree.md` |
| `codex exec`, prompt 규칙, timeout, stdout/stderr 수집 | `docs/architecture/codex-runner.md` |
| thread/job/run 저장 모델 | `docs/architecture/storage.md` |
| 승인 흐름, 수정/PR 승인 원칙 | `docs/architecture/approvals.md` |

긴 배경이 정말 필요할 때만 `docs/reference/pangi-platform-design-python.md`를 읽는다.

## 실행 절차

1. 사용자의 요청이 계획만 필요한지, 구현 전 검토가 필요한지, 승인 전 단계인지 확인한다.
2. 현재 작업이 체크리스트 몇 단계에 해당하는지 찾고, 선행 단계가 있으면 명시한다.
3. `.codex/prompts/researcher.md` 관점으로 현재 코드/문서/제약을 정리한다.
4. `.codex/prompts/planner.md` 관점으로 추천 접근, 변경 범위, 단계별 실행 순서, 검증 방법, 문서 업데이트 필요 여부를 정리한다.
5. `.codex/prompts/reviewer.md` 관점으로 리스크, 범위 이탈, 누락된 테스트, 운영/보안 영향을 검토한다.
6. 세 관점을 합쳐 최종 추천 방향과 다음 단계를 요약한다.
7. 사용자가 계획만 요청했거나 승인이 필요한 상황이면 파일을 수정하지 않고 응답으로 마친다.

## 팡이 전용 판단 기준

- `poppangbot/`은 Slack 연결 샘플로 취급한다. 팡이 본체와 혼동하지 않는다.
- 기본 구현 방향은 Python/FastAPI다. 사용자가 명시적으로 요청하지 않으면 TypeScript 전환을 제안하지 않는다.
- 1차 MVP에서는 코드 수정, PR 생성, Notion 연동을 기본 범위에 넣지 않는다.
- 수정 흐름을 논의하더라도 read-only 분석 -> Slack 승인 -> workspace-write -> diff -> PR 승인 순서를 유지한다.
- Codex가 source repo에서 직접 실행되거나 commit/push/PR을 직접 수행하는 방향을 허용하지 않는다.
- 사용자 입력을 shell command로 직접 실행하는 제안, `shell=True` 제안, timeout 없는 외부 명령 제안은 금지한다.

## 출력 형식

기본 응답 형식은 아래 순서를 따른다.

```text
## Researcher 관점
## Planner 관점
## Reviewer 관점
## 최종 추천 방향
```

각 섹션에는 아래를 반영한다.

- 확인한 사실과 추정을 분리한다.
- 관련 파일이나 문서 경로를 적는다.
- 체크리스트 단계와 선행 조건을 적는다.
- 테스트/검증 방법을 적는다.
- 문서 업데이트가 필요하면 빠뜨리지 않는다.

필요하면 마지막에 아래 문장을 포함한다.

```text
위 계획으로 진행해도 될까요? 승인 전까지 파일은 수정하지 않겠습니다.
```

## 금지 사항

- `.env`, token, signing secret, Codex auth 파일을 열람하거나 출력하지 않는다.
- 확인하지 않은 구조를 이미 존재하는 코드처럼 단정하지 않는다.
- 체크리스트 순서를 무시한 큰 설계를 가볍게 권하지 않는다.
- README, 작은 기준 문서, 체크리스트보다 긴 reference 문서를 우선하지 않는다.
- 사용자가 계획만 요청한 상황에서 파일 생성, 수정, 삭제를 하지 않는다.
