# Job Worker

## 역할

Job Worker는 Slack 3초 응답 제한과 긴 작업 실행을 분리한다.

Slack route는 요청을 접수하고 빠르게 200 OK를 반환한다. 실제 Codex 실행, worktree 생성, 결과 수집은 worker가 담당한다.

## MVP 구조

처음에는 in-process queue로 시작한다.

```text
FastAPI route
-> create job
-> enqueue job_id
-> return 200 OK

worker
-> dequeue job_id
-> create worktree
-> run codex
-> collect result
-> reply slack
```

## 상태

```text
queued
running
succeeded
failed
timed_out
cancelled
waiting_approval
rejected
```

## 동시 실행 정책

MVP 기본값:

- 전체 동시 실행: 1개 또는 2개
- repo별 동시 실행: 1개

PopPang-iOS 같은 하나의 큰 repo를 대상으로 하면 repo별 1개가 안전하다.

## 실패 처리

- worktree 생성 실패: `failed`
- Codex non-zero exit: `failed`
- Codex timeout: `timed_out`
- Slack 응답 실패: job 결과는 저장하고 Slack error를 기록

## 중간 상태 메시지

너무 자주 보내지 않는다. 상태 전환 기준으로만 보낸다.

```text
팡이가 요청을 접수했습니다.
안전한 작업 공간을 만들고 있습니다.
팡이가 read-only로 코드를 읽고 있습니다.
분석 결과를 정리하고 있습니다.
```

## 테스트 기준

- queued job이 running으로 바뀐다.
- 성공 job이 succeeded가 된다.
- 실패 job이 failed가 된다.
- timeout job이 timed_out이 된다.
- Slack route가 worker 완료를 기다리지 않는다.
