# Scheduler

## 역할

Scheduler는 관리자가 미리 등록한 요청을 정해진 시간에 자동으로 실행한다.

핵심 원칙은 새 실행 엔진을 만들지 않는 것이다. Scheduler는 시간이 된 예약 작업을 감지한 뒤 synthetic Slack request를 만들고, 기존 `SubmitSlackRequestUseCase` 흐름에 태운다.

```text
scheduled_tasks
-> scheduler tick
-> scheduled_task_runs claim
-> Slack root message 생성
-> SubmitSlackRequestUseCase
-> 입력 가드레일 / Orchestrator
-> Codex chat 또는 AgentJob
-> 기존 Slack thread 응답
```

## MVP 범위

- 관리자 페이지 `/pangi-admin/schedules`에서만 예약 작업을 생성한다.
- 지원 반복은 `once`, `daily`, `weekly`다.
- 시간대는 IANA timezone 이름을 사용하고 기본 예시는 `Asia/Seoul`이다.
- 실제 실행 여부는 `PANGI_SCHEDULER_ENABLED`로 제어한다. 기본값은 `0`이다.
- tick loop는 in-process task로 시작한다.
- 예약 실행마다 새 Slack root message를 만들고, 그 message `ts`를 thread id로 사용한다.

## 의도적으로 하지 않는 것

- Slack 자연어로 스케줄 생성/수정하기
- cron expression 지원
- 외부 웹/URL 분석 자동 실행
- 코드 수정, PR 생성, 배포 자동 실행
- 여러 서버 인스턴스 간 분산 scheduler coordination

## 저장 구조

### `scheduled_tasks`

예약 작업 정의를 저장한다.

```text
id TEXT PRIMARY KEY
name TEXT NOT NULL
enabled INTEGER NOT NULL
team_id TEXT NOT NULL
channel_id TEXT NOT NULL
requester_user_id TEXT NOT NULL
prompt TEXT NOT NULL
schedule_type TEXT NOT NULL
timezone TEXT NOT NULL
time_of_day TEXT
days_of_week TEXT
run_at TEXT
next_run_at TEXT
last_run_at TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
```

`schedule_type`은 `once`, `daily`, `weekly`를 사용한다.
`days_of_week`는 월요일 `0`부터 일요일 `6`까지의 값을 쉼표로 저장한다.

### `scheduled_task_runs`

예약 작업의 각 실행 시도를 저장한다.

```text
id TEXT PRIMARY KEY
scheduled_task_id TEXT NOT NULL
scheduled_for TEXT NOT NULL
status TEXT NOT NULL
event_id TEXT NOT NULL UNIQUE
slack_thread_ts TEXT
job_id TEXT
classification TEXT
error_message TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
UNIQUE(scheduled_task_id, scheduled_for)
FOREIGN KEY(scheduled_task_id) REFERENCES scheduled_tasks(id)
FOREIGN KEY(job_id) REFERENCES agent_jobs(id)
```

`UNIQUE(scheduled_task_id, scheduled_for)`는 같은 예약 시각을 두 번 실행하지 않기 위한 claim 키다.

## 안전 규칙

- Scheduler가 Codex runner를 직접 호출하지 않는다.
- Scheduler가 repository path나 command를 직접 만들지 않는다.
- 예약 prompt도 기존 입력 가드레일과 Orchestrator를 반드시 통과한다.
- Slack user/channel allowlist를 관리자 등록 시점에 검증한다.
- `PANGI_SCHEDULER_ENABLED=0`이면 저장된 스케줄이 있어도 자동 실행하지 않는다.
- 예약 실행 실패는 `scheduled_task_runs.error_message`에 남긴다.
- repo 분석 예약은 기존 `AgentJob`과 job queue를 사용하므로 기존 timeout, read-only sandbox, repo 동시 실행 제한을 따른다.
- 서버가 오래 멈췄다가 다시 시작해도 과거 예약을 연속 백필하지 않고, due schedule을 한 번 실행한 뒤 다음 미래 실행 시각으로 넘어간다.

## 설정

```env
PANGI_SCHEDULER_ENABLED=0
PANGI_SCHEDULER_TICK_SECONDS=30
```

## 테스트 기준

- due schedule만 claim된다.
- 같은 `scheduled_task_id + scheduled_for`는 중복 claim되지 않는다.
- `once` schedule은 claim 후 비활성화된다.
- chat schedule은 Slack thread에 답하고 run을 `succeeded`로 기록한다.
- repo analysis schedule은 기존 `AgentJob`을 만들고 run에 `job_id`를 저장한다.
