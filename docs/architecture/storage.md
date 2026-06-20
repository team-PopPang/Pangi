# Storage

## 역할

Storage 계층은 Slack thread, job, Codex 실행 결과를 추적한다.

MVP에서는 단순하게 시작하되, repository 인터페이스를 분리해서 SQLite에서 PostgreSQL로 옮기기 쉽게 만든다.

## MVP 저장 대상

### SlackThread

```text
id
team_id
channel_id
thread_ts
last_job_id
created_at
updated_at
```

### AgentJob

```text
id
event_id
slack_thread_id
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

### CodexRun

```text
id
job_id
mode
command
prompt
stdout
stderr
exit_code
timed_out
started_at
finished_at
```

## 중복 방지

Slack `event_id`는 unique하게 저장한다. Slack retry가 와도 같은 event로 job이 여러 개 만들어지지 않아야 한다.

## 로그 저장 원칙

- MVP에서는 DB text 컬럼에 저장해도 된다.
- Slack에 보낼 때는 요약하거나 길이를 제한한다.
- secret redaction 후 외부 출력한다.
- 운영 규모가 커지면 원본 로그는 object storage로 옮긴다.

## 테스트 기준

- thread 생성과 재조회
- job 생성
- job 상태 변경
- Codex run 저장
- event id 중복 방지
