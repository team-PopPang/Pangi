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
