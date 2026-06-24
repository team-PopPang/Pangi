# Codex Runner

## 역할

Codex Runner는 서버에서 `codex exec`와 `codex exec resume`을 안전하게 실행하고 결과를 수집한다.

핵심 기준은 아래와 같다.

- Slack thread 1개에는 활성 Codex session이 정확히 1개만 연결된다.
- 첫 turn은 `codex exec`로 새 session을 만들고, 후속 turn은 `codex exec resume`으로 이어간다.
- 같은 Slack thread의 일반 대화와 repo 분석은 같은 session을 공유한다.

## MVP 명령

새 session 시작:

```bash
codex exec \
  -C {thread_workspace_path} \
  --skip-git-repo-check \
  --sandbox read-only \
  --json \
  --output-last-message {output_path} \
  -c model_reasoning_effort="{effort}" \
  --model {model} \
  "{prompt}"
```

기존 session 이어쓰기:

```bash
codex exec resume {codex_session_id} \
  --json \
  --output-last-message {output_path} \
  -c model_reasoning_effort="{effort}" \
  --model {model} \
  "{prompt}"
```

session archive:

```bash
codex archive {codex_session_id}
```

repo read-only 분석은 실제 코드를 읽고 근거를 정리하는 단계이므로 기본 모델은 `PANGI_ANALYSIS_MODEL=gpt-5.5`를 사용한다.
일반 대화와 orchestrator 라우팅은 각각 `PANGI_CHAT_MODEL`, `PANGI_ORCHESTRATOR_MODEL` 기본값 `gpt-5.4-mini`와 reasoning `low`를 사용한다.
repo 분석은 기본 reasoning `high`를 사용한다.

팡이를 개발하는 Codex의 `.codex/config.toml`은 팡이 런타임 설정이 아니다.
팡이가 실행하는 `codex exec`는 각 호출에서 `-c model_reasoning_effort="..."`를 명시해 프로젝트/사용자 Codex 설정이 몰래 상속되지 않게 한다.

수정 모드는 1차 MVP에서 실행하지 않는다.
나중에 Slack 승인 이후에만 사용한다.

## 실행 규칙

- `asyncio.create_subprocess_exec`를 사용한다.
- `shell=True`를 사용하지 않는다.
- 명령은 argv list로 구성한다.
- 사용자 입력은 shell이 아니라 prompt 인자로만 전달한다.
- 첫 turn의 cwd는 항상 서버가 만든 thread workspace여야 한다.
- thread workspace가 git repo가 아니어도 `--skip-git-repo-check`로 실행한다.
- timeout 없는 실행을 만들지 않는다.

## session 수집 규칙

- `--json` stdout에서 `thread.started.thread_id`를 읽어 실제 Codex session id를 저장한다.
- 최종 assistant 메시지는 `--output-last-message` 파일에서 읽는다.
- resume turn에서 새 session id가 오지 않으면 기존 `codex_session_id`를 그대로 사용한다.

## 수집 항목

```text
stdout
stderr
exit_code
timed_out
started_at
finished_at
command
prompt
codex_session_id
workspace_path
```

## prompt 필수 규칙

Codex chat과 read-only 분석 prompt는 `pangi/src/pangi/prompts/` 아래 Markdown 파일을 조합해 만든다.

- 일반 대화와 repo 분석은 더 이상 최근 `thread_messages`를 통째로 재주입하지 않는다.
- 같은 Slack thread의 연속성은 Codex session이 담당한다.
- repo 분석 prompt에는 `repo_path`를 명시해 thread workspace 안에서 어느 repo checkout을 읽어야 하는지 알려준다.
- 파일을 수정하지 말 것
- 근거 파일 경로를 표시할 것
- 확인한 사실과 추정을 분리할 것
- 결론을 먼저 쓸 것
- 검증 방법을 쓸 것
- secret, token, `.env` 내용을 출력하지 말 것

## 실패 처리

- exit code가 0이 아니면 failed
- timeout이면 process terminate 후 필요 시 kill
- stderr는 저장하되 Slack에는 요약만 보낸다.
- session archive 실패는 `archive_failed`로 기록하고, 다음 turn은 새 session을 만들 수 있게 thread active 연결은 해제한다.
- Slack/Notion 출력 전 secret redaction을 통과시킨다.

## 테스트 기준

- 새 session의 stdout 수집
- `thread.started`에서 session id 추출
- resume 명령 구성
- stderr 수집
- non-zero exit 처리
- timeout 처리
- argv list로 명령 구성
- shell injection 문자열이 prompt로만 전달됨
