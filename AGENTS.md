# AGENTS.md

이 저장소에서 작업하는 에이전트는 아래 규칙을 따른다.

## 프로젝트 목적

팡이는 PopPang 팀 전용 Slack 기반 개발 에이전트다.

1차 MVP의 목표는 Slack에서 팡이에게 분석 요청을 보내면, 서버가 격리된 worktree에서 `codex exec --sandbox read-only`로 코드를 분석하고 결과를 Slack thread에 반환하는 것이다.

처음부터 큰 사내 플랫폼을 만들지 않는다. PopPang 규모에 맞게 작고 안전한 MVP부터 만든다.

## 현재 구조

```text
README.md                                      프로젝트 입구 문서
docs/mvp/overview.md                           MVP 기준 문서
docs/implementation-checklist.md               구현 체크리스트
docs/architecture/                             컴포넌트별 설계 문서
docs/security/safety-rules.md                  공통 안전 규칙
docs/reference/pangi-platform-design-python.md 긴 설계 초안 보관본
poppangbot/                                    Slack 연결 검증용 FastAPI 샘플
```

중요:

- `poppangbot/`은 완성된 팡이 플랫폼이 아니다.
- `poppangbot/`은 Slack slash command와 app mention 연결을 확인하기 위한 샘플이다.
- 실제 팡이 MVP는 Python/FastAPI 기반으로 별도 `pangi/` 패키지에 구현하는 것을 기본 방향으로 한다.

## SQLite 저장소 구조

현재 팡이 MVP의 SQLite 저장소 구현 기준은 `pangi/src/pangi/repository/job_repository_sqlite_impl.py`다.
테이블 구조를 바꿀 때는 모델, repository, 테스트, 관련 문서를 함께 갱신한다.

### `slack_threads`

Slack thread 단위 대화 컨텍스트를 저장한다.

```text
id TEXT PRIMARY KEY
team_id TEXT NOT NULL
channel_id TEXT NOT NULL
thread_ts TEXT NOT NULL
last_job_id TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
UNIQUE(team_id, channel_id, thread_ts)
```

### `agent_jobs`

Slack 요청 하나를 팡이 job 하나로 저장한다.

```text
id TEXT PRIMARY KEY
event_id TEXT NOT NULL UNIQUE
slack_thread_id TEXT NOT NULL
slack_team_id TEXT NOT NULL
slack_channel_id TEXT NOT NULL
slack_thread_ts TEXT NOT NULL
slack_message_ts TEXT
requester_user_id TEXT NOT NULL
job_type TEXT NOT NULL
status TEXT NOT NULL
repo_key TEXT NOT NULL
prompt TEXT NOT NULL
worktree_path TEXT
stdout TEXT
stderr TEXT
error_message TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
FOREIGN KEY(slack_thread_id) REFERENCES slack_threads(id)
```

`slack_message_ts`는 Slack app mention 원본 메시지의 `ts`다. 원본 메시지에 단 `eyes` reaction을 완료 reaction으로 교체할 때 사용하며, slash command나 legacy job에서는 비어 있을 수 있다.

`status`는 현재 `queued`, `running`, `succeeded`, `failed`, `timed_out`, `cancelled`, `waiting_approval`, `rejected`를 사용한다.
`job_type`은 현재 `analyze`, `edit_requested`, `pr_summary`, `troubleshooting`, `xcodebuild_failure`를 사용한다.

### `codex_runs`

job 안에서 실행된 Codex 실행 기록을 저장한다.

```text
id TEXT PRIMARY KEY
job_id TEXT NOT NULL
mode TEXT NOT NULL
command TEXT NOT NULL
prompt TEXT NOT NULL
stdout TEXT
stderr TEXT
exit_code INTEGER
timed_out INTEGER NOT NULL
started_at TEXT NOT NULL
finished_at TEXT
FOREIGN KEY(job_id) REFERENCES agent_jobs(id)
```

## 구현 원칙

- Python/FastAPI 기반을 우선한다.
- TypeScript로 전환하지 않는다. 사용자가 명시적으로 요청한 경우에만 재검토한다.
- 1차 MVP에서는 코드 수정 기능을 만들지 않는다.
- 1차 MVP에서는 PR 생성 기능을 만들지 않는다.
- 1차 MVP에서는 Notion 연동을 필수로 만들지 않는다.
- 먼저 Slack 수신 -> background job -> worktree -> Codex read-only -> Slack thread 응답 흐름을 완성한다.
- 기능을 만들 때는 [docs/implementation-checklist.md](docs/implementation-checklist.md)의 순서를 따른다.
- 구현이 끝난 항목은 체크리스트를 `[x]`로 갱신한다.

## 문서 라우팅

작업을 시작할 때 모든 문서를 읽지 않는다. 작업 종류에 맞는 문서만 먼저 읽는다.

### 먼저 읽을 문서

- 전체 방향 확인: [docs/mvp/overview.md](docs/mvp/overview.md)
- 구현 순서 확인: [docs/implementation-checklist.md](docs/implementation-checklist.md)
- 안전 규칙 확인: [docs/security/safety-rules.md](docs/security/safety-rules.md)

### 작업별 문서

- Slack 수신/응답 작업: [docs/architecture/slack.md](docs/architecture/slack.md)
- 요청 분류/흐름 제어 작업: [docs/architecture/orchestrator.md](docs/architecture/orchestrator.md)
- background job 작업: [docs/architecture/jobs.md](docs/architecture/jobs.md)
- Codex 실행 작업: [docs/architecture/codex-runner.md](docs/architecture/codex-runner.md)
- git worktree 작업: [docs/architecture/git-worktree.md](docs/architecture/git-worktree.md)
- 저장소/job 모델 작업: [docs/architecture/storage.md](docs/architecture/storage.md)
- 승인 흐름 작업: [docs/architecture/approvals.md](docs/architecture/approvals.md)
- 보안/권한/secret 작업: [docs/security/safety-rules.md](docs/security/safety-rules.md)

### 참고 문서

- 긴 배경이 필요할 때만: [docs/reference/pangi-platform-design-python.md](docs/reference/pangi-platform-design-python.md)

### 문서 사용 규칙

- 체크리스트는 현재 위치에서 유지한다.
- 중복 설명을 늘리지 않는다. 공통 안전 규칙은 [docs/security/safety-rules.md](docs/security/safety-rules.md)로 모은다.
- 긴 reference 문서와 작은 기준 문서가 충돌하면 `AGENTS.md`, `README.md`, 체크리스트, 작은 문서 순으로 우선한다.

## 안전 규칙

- 공통 안전 규칙은 [docs/security/safety-rules.md](docs/security/safety-rules.md)를 우선한다.
- `.env`, token, signing secret, Codex auth 파일을 열람하거나 출력하지 않는다.
- secret 값을 문서, 로그, 테스트, Slack 응답 예시에 쓰지 않는다.
- 사용자의 Slack 메시지를 shell command로 직접 실행하지 않는다.
- `subprocess`를 사용할 때는 `shell=True`를 사용하지 않는다.
- `codex exec`는 반드시 argv list로 실행한다.
- 분석 작업은 `--sandbox read-only`를 사용한다.
- 수정 작업은 Slack 승인 이후에만 `--sandbox workspace-write`를 사용한다.
- Codex 실행 위치는 항상 서버가 만든 worktree여야 한다.
- 원본 source repo에서 Codex를 직접 실행하지 않는다.
- main/develop 같은 기본 브랜치를 직접 수정하지 않는다.
- Codex가 직접 commit/push/PR 생성을 하게 두지 않는다. git 상태와 PR 생성은 서버가 통제한다.
- Slack user allowlist, channel allowlist, repo allowlist를 우선 구현한다.
- timeout 없는 외부 명령 실행을 만들지 않는다.

## 문서 작성 규칙

- README는 처음 보는 사람이 이해하기 쉬운 입구 문서로 유지한다.
- 문서 지도는 이 `AGENTS.md`에 둔다.
- `docs/README.md`는 만들지 않는다.
- 긴 설계 설명은 `docs/reference/` 아래에 보관한다.
- 실제 구현 기준은 작은 문서로 쪼개 `docs/mvp/`, `docs/architecture/`, `docs/security/` 아래에 둔다.
- 구현 추적은 `docs/implementation-checklist.md`에서 한다.
- 새 기능을 구현해 동작이 바뀌면 README 또는 관련 문서를 함께 갱신한다.
- 문서는 한국어로 작성한다.
- 예시 명령에는 실제 secret 값을 넣지 않는다.

## 테스트 규칙

가능하면 변경 범위에 맞는 테스트를 추가하거나 갱신한다.

`poppangbot/` 샘플 변경 시:

```bash
cd poppangbot
pytest
```

새 `pangi/` 패키지 구현 후에는 해당 패키지의 테스트 명령을 README와 체크리스트에 명시한다.

테스트를 실행하지 못했다면 최종 응답에 이유를 적는다.

## 작업 흐름

작업 전:

- 관련 README와 체크리스트를 확인한다.
- 이 문서의 "문서 라우팅"에 따라 필요한 세부 문서만 읽는다.
- 민감 파일을 열지 않는다.
- 현재 작업이 몇 단계 체크리스트에 해당하는지 확인한다.

작업 중:

- 변경은 필요한 파일에만 제한한다.
- 샘플 봇과 실제 플랫폼 코드를 섞지 않는다.
- 실패 케이스, timeout, secret redaction을 같이 고려한다.

작업 후:

- 가능한 테스트를 실행한다.
- 구현된 체크리스트 항목을 갱신한다.
- 최종 응답에 변경 파일, 검증 결과, 남은 작업을 짧게 정리한다.

## 참고 우선순위

작업 판단이 애매하면 아래 순서로 따른다.

1. 사용자 요청
2. 이 `AGENTS.md`
3. [README.md](README.md)
4. [docs/mvp/overview.md](docs/mvp/overview.md)
5. [docs/implementation-checklist.md](docs/implementation-checklist.md)
6. 작업별 `docs/architecture/` 문서
7. [docs/security/safety-rules.md](docs/security/safety-rules.md)
8. [docs/reference/pangi-platform-design-python.md](docs/reference/pangi-platform-design-python.md)
9. [poppangbot/README.md](poppangbot/README.md)

긴 reference 문서가 작은 기준 문서나 체크리스트와 충돌하면 작은 기준 문서와 체크리스트를 우선한다.
