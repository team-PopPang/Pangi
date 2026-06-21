# Codex Runner

## 역할

Codex Runner는 서버에서 `codex exec`를 안전하게 실행하고 결과를 수집한다.

## MVP 명령

분석 모드:

```bash
codex exec -C {worktree_path} --sandbox read-only --model {PANGI_ANALYSIS_MODEL} "{prompt}"
```

repo read-only 분석은 실제 코드를 읽고 근거를 정리하는 단계이므로 기본 모델은 `PANGI_ANALYSIS_MODEL=gpt-5.5`를 사용한다.
일반 대화와 orchestrator 라우팅은 각각 `PANGI_CHAT_MODEL`, `PANGI_ORCHESTRATOR_MODEL` 기본값 `gpt-5.4-mini`를 사용한다.

수정 모드는 1차 MVP에서 실행하지 않는다. 나중에 Slack 승인 이후에만 사용한다.

```bash
codex exec -C {worktree_path} --sandbox workspace-write "{prompt}"
```

## 실행 규칙

- `asyncio.create_subprocess_exec`를 사용한다.
- `shell=True`를 사용하지 않는다.
- 명령은 argv list로 구성한다.
- 사용자 입력은 shell이 아니라 prompt 인자로만 전달한다.
- cwd는 항상 서버가 만든 worktree여야 한다.
- timeout 없는 실행을 만들지 않는다.

## 수집 항목

```text
stdout
stderr
exit_code
timed_out
started_at
finished_at
duration_ms
command
prompt
```

## prompt 필수 규칙

Codex chat과 read-only 분석 prompt는 `pangi/src/pangi/prompts/` 아래 Markdown 파일을 조합해 만든다.

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
- Slack/Notion 출력 전 secret redaction을 통과시킨다.

## 테스트 기준

- stdout 수집
- stderr 수집
- non-zero exit 처리
- timeout 처리
- argv list로 명령 구성
- shell injection 문자열이 prompt로만 전달됨
