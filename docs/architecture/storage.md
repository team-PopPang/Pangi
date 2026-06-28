# Storage

## 역할

Storage 계층은 Slack thread, thread 대화 turn, active Codex session, agent job, Codex 실행 결과를 추적한다.

핵심 기준은 아래 두 문장이다.

- Slack thread 1개에는 활성 Codex session이 최대 1개만 있다.
- Slack thread 1개에는 활성 thread workspace가 최대 1개만 있다.

## MVP 저장 대상

### SlackThread

```text
id
team_id
channel_id
thread_ts
last_job_id
active_codex_session_id
created_at
updated_at
```

`active_codex_session_id`는 현재 thread에 연결된 활성 Codex session을 가리킨다.
idle timeout이 지나 archive되면 `NULL`로 비운다.

### ThreadMessage

```text
id
slack_thread_id
role
text
message_ts
event_id
source_job_id
created_at
```

`role`은 `user`, `assistant`를 사용한다.
이제 `thread_messages`는 매 요청마다 Codex prompt에 재주입하는 주 저장소가 아니라, 관리자 확인과 감사 로그에 가까운 역할을 맡는다.

### CodexSession

```text
id
slack_thread_id
codex_thread_id
workspace_path
status
last_used_at
expires_at
archived_at
created_at
updated_at
```

`status`는 현재 `active`, `expired`, `archived`, `archive_failed`를 사용한다.

`codex_thread_id`는 Codex CLI가 `thread.started` 이벤트로 돌려준 실제 session id다.
`workspace_path`는 thread 전용 workspace root 경로다.

### AgentJob

```text
id
event_id
slack_thread_id
codex_session_id
slack_team_id
slack_channel_id
slack_thread_ts
slack_message_ts
requester_user_id
job_type
status
repo_key
prompt
worktree_path
stdout
stderr
error_message
created_at
updated_at
```

`codex_session_id`는 job이 연결된 내부 CodexSession record id다.
`worktree_path`는 이름은 그대로 두지만, 의미는 "thread workspace 안에서 실제로 분석한 repo checkout 경로"다.

### CodexRun

```text
id
job_id
codex_session_id
mode
command
prompt
stdout
stderr
exit_code
timed_out
workspace_path
started_at
finished_at
```

`workspace_path`는 Codex가 실제로 turn을 실행한 thread workspace root다.

### ScheduledTask

```text
id
name
enabled
team_id
channel_id
requester_user_id
prompt
schedule_type
timezone
time_of_day
days_of_week
run_at
next_run_at
last_run_at
created_at
updated_at
```

`schedule_type`은 `once`, `daily`, `weekly`를 사용한다.
예약 작업은 Codex runner를 직접 실행하지 않고, 시간이 되면 synthetic Slack request를 만들어 기존 입력 가드레일과 `SubmitSlackRequestUseCase` 흐름을 통과한다.

### ScheduledTaskRun

```text
id
scheduled_task_id
scheduled_for
status
event_id
slack_thread_ts
job_id
classification
error_message
created_at
updated_at
```

`status`는 `claimed`, `submitted`, `succeeded`, `failed`를 사용한다.
`event_id`는 `schedule:{scheduled_task_id}:{scheduled_for}` 형식으로 만들어 기존 Slack retry 중복 방지와 같은 방식으로 job 중복을 막는다.

### EvalCaseDefinition

```text
id
suite
case_id
name
tags
case_json
created_at
updated_at
```

Eval case DSL의 최신 snapshot을 저장한다.
`suite, case_id`는 unique하게 유지한다.

### EvalRun

```text
id
suite
mode
status
total_count
passed_count
failed_count
prompt_fingerprint
model_fingerprint
provider_fingerprint
started_at
finished_at
created_at
updated_at
```

`status`는 `succeeded`, `failed`를 사용한다.
fingerprint는 prompt/model/provider 변경 전후 회귀 비교를 위한 해시이며 secret 값을 저장하지 않는다.

### EvalCaseResultRecord

```text
id
eval_run_id
suite
case_id
name
status
classification
job_id
job_repo_key
failures
slack_messages
created_at
```

`status`는 `passed`, `failed`를 사용한다.
`failures`와 `slack_messages`는 JSON 배열 문자열로 저장한다.

### EvalTraceEventRecord

```text
id
eval_case_result_id
event_index
name
attributes
created_at
```

case 실행 중 관찰한 provider, queue, Codex, Slack boundary event를 저장한다.
`eval_case_result_id, event_index`는 unique하다.

### EvalRedTeamCandidate

```text
id
suite
case_id
name
input
attack_surface
status
case_json
created_at
updated_at
approved_at
```

`status`는 `draft`, `approved`, `rejected`를 사용한다.
승인된 후보는 admin/scheduled Eval suite에 함께 포함된다.

## session lifecycle

기본 idle timeout은 `PANGI_CODEX_SESSION_IDLE_TIMEOUT_SECONDS`이고 기본값은 3600초다.

```text
Slack thread 첫 요청
-> Codex 새 session 생성
-> codex_sessions row 생성
-> slack_threads.active_codex_session_id 연결

같은 thread 후속 요청
-> active session 조회
-> idle timeout 전이면 codex exec resume 사용
-> last_used_at, expires_at 갱신

idle timeout 경과
-> codex archive 시도
-> codex_sessions 상태 변경
-> slack_threads.active_codex_session_id 해제
-> thread workspace cleanup
```

## 중복 방지

Slack `event_id`는 unique하게 저장한다.
Slack retry가 와도 같은 event로 job이나 user message가 여러 개 만들어지지 않아야 한다.

`slack_message_ts`는 app mention 원본 메시지의 reaction을 완료 상태로 바꾸기 위한 값이다.
slash command처럼 원본 메시지 reaction을 관리하지 않는 요청에서는 비어 있을 수 있다.

Scheduler는 `scheduled_task_runs`의 `UNIQUE(scheduled_task_id, scheduled_for)`와 `event_id` unique 제약으로 같은 예약 시각을 중복 실행하지 않는다.

Eval은 `eval_trace_events`의 `UNIQUE(eval_case_result_id, event_index)`로 같은 case 결과 안의 trace 순서를 중복 저장하지 않는다.

## 로그 저장 원칙

- MVP에서는 DB text 컬럼에 저장해도 된다.
- Slack에 보낼 때는 요약하거나 길이를 제한한다.
- secret redaction 후 외부 출력한다.
- 운영 규모가 커지면 원본 로그는 object storage로 옮긴다.

## 테스트 기준

- thread 생성과 재조회
- thread message 저장과 조회
- active Codex session 생성과 archive
- job 생성과 session 연결
- job 상태 변경
- Codex run 저장
- event id 중복 방지
- 예약 작업 생성과 due 조회
- 예약 실행 claim 중복 방지
- Eval run/result/trace 저장과 조회
- Red Team 후보 생성, 승인, 거절
