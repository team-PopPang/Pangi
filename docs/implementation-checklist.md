# 팡이 구현 체크리스트

이 문서는 팡이 MVP를 구현하면서 하나씩 체크하기 위한 작업 목록이다.

기준은 "PopPang 규모에 맞는 현실적인 Python/FastAPI 기반 MVP"다. 처음부터 큰 플랫폼을 만들지 않고, Slack에서 팡이가 요청을 받고 `codex exec --sandbox read-only`로 분석한 뒤 Slack thread에 답하는 흐름을 먼저 완성한다.

## 체크 규칙

- `[ ]`: 아직 시작하지 않음
- `[x]`: 완료
- 항목을 체크하기 전에 바로 아래 "완료 기준"을 만족했는지 확인한다.
- 구현 중 새로 발견한 작업은 해당 단계 아래에 체크박스로 추가한다.
- 민감한 값은 문서에 쓰지 않는다. `.env`, token, signing secret, Codex auth 정보는 절대 기록하지 않는다.

## 전체 구현 순서 요약

- [x] 0단계: MVP 범위 확정
- [x] 1단계: 현재 `poppangbot` 샘플 안정화
- [x] 2단계: 팡이 서버 프로젝트 구조 만들기
- [x] 3단계: 설정과 allowlist 기반 만들기
- [x] 4단계: Slack 요청 수신 계층 만들기
- [x] 5단계: 작업 모델과 job 저장소 만들기
- [x] 6단계: background job worker 만들기
- [ ] 7단계: git worktree manager 만들기
- [ ] 8단계: Codex runner 만들기
- [ ] 9단계: Slack thread 응답 연결하기
- [ ] 10단계: 1차 MVP end-to-end 검증
- [ ] 11단계: 안전장치 강화
- [ ] 12단계: Notion report 붙이기
- [ ] 13단계: 수정 승인 흐름 붙이기
- [ ] 14단계: PR 생성 흐름 붙이기
- [ ] 15단계: 운영/배포 정리

---

## 0단계: MVP 범위 확정

### 목표

구현 전에 "이번에 만들 것"과 "나중에 만들 것"을 분리한다.

### 체크리스트

- [x] 1차 MVP 목표를 아래 한 문장으로 확정한다.
  - 완료 기준: README 또는 이 문서에 같은 문장이 적혀 있다.
  - 권장 문장: "Slack에서 팡이에게 분석 요청을 보내면, 서버가 격리된 worktree에서 Codex read-only 분석을 실행하고 결과를 Slack thread에 반환한다."

- [x] `poppangbot/`의 역할을 "Slack 연결 샘플"로 확정한다.
  - 완료 기준: README에 `poppangbot`이 완성 플랫폼이 아니라 샘플이라고 적혀 있다.

- [x] 1차 MVP에서 코드 수정 기능을 제외한다.
  - 완료 기준: 수정은 13단계 이후 작업으로 분리되어 있다.

- [x] 1차 MVP에서 PR 생성 기능을 제외한다.
  - 완료 기준: PR 생성은 14단계 이후 작업으로 분리되어 있다.

- [x] 1차 MVP에서 Notion 저장을 제외하거나 optional로 둔다.
  - 완료 기준: Notion 없이도 Slack 분석 응답까지 end-to-end 성공할 수 있다.

- [x] 참고 사례에서 가져올 원칙만 정리한다.
  - 완료 기준: AB180/당근/Karby 사례를 복제하지 않고, Slack 중심 작업 접수, orchestrator, 권한/격리, 실패 처리 정도만 MVP에 반영한다.

- [x] `codex exec` 실행 방식의 기본 원칙을 확정한다.
  - 완료 기준: 분석은 `--sandbox read-only`, 수정은 승인 후 `--sandbox workspace-write`라고 문서에 적혀 있다.

- [x] Codex가 commit/push/PR 생성을 직접 하지 않는 원칙을 확정한다.
  - 완료 기준: git commit/push/PR은 서버가 담당한다고 문서에 적혀 있다.

### 완료 기준

- [x] 1차 MVP 범위가 누구나 이해할 수 있게 README에 적혀 있다.
- [x] 제외 범위가 명확해서 구현 중 범위가 커지지 않는다.

---

## 1단계: 현재 `poppangbot` 샘플 안정화

### 목표

기존 FastAPI 샘플이 Slack 연결 검증 도구로 계속 동작하도록 유지한다.

### 체크리스트

- [x] `poppangbot/README.md`를 읽고 현재 실행 방법을 확인한다.
  - 완료 기준: 로컬 실행 명령과 `/health` 확인 방법을 알고 있다.

- [x] `poppangbot/app.py`의 Slack signature 검증 흐름을 확인한다.
  - 완료 기준: `X-Slack-Request-Timestamp`, `X-Slack-Signature`, body 기반 HMAC 검증 위치를 확인했다.

- [x] `.env.example`에 필요한 환경변수 이름이 정리되어 있는지 확인한다.
  - 완료 기준: 실제 secret 값 없이 변수명만 있다.

- [x] 기존 테스트를 실행한다.
  - 명령 예시:
    ```bash
    cd poppangbot
    pytest
    ```
  - 완료 기준: 기존 테스트가 통과하거나, 실패 이유가 문서화되어 있다.

- [x] Slack slash command 테스트 흐름을 확인한다.
  - 완료 기준: `/slack/commands`가 유효한 Slack signature에서 테스트 응답을 반환한다.

- [x] Slack app mention 테스트 흐름을 확인한다.
  - 완료 기준: `/slack/events`가 `url_verification`과 `app_mention`을 처리한다.

- [x] `poppangbot`에 팡이 플랫폼 본체 기능을 계속 넣을지, 별도 패키지로 분리할지 결정한다.
  - 권장: 별도 `pangi/` 패키지로 분리
  - 완료 기준: README 또는 설계 문서에 결정이 적혀 있다.

### 완료 기준

- [x] 샘플 봇이 로컬에서 실행된다.
- [x] 기존 Slack 연결 테스트가 깨지지 않는다.
- [x] 샘플과 실제 팡이 플랫폼의 역할이 분리되어 있다.

---

## 2단계: 팡이 서버 프로젝트 구조 만들기

### 목표

실제 팡이 MVP를 구현할 Python/FastAPI 프로젝트 뼈대를 만든다.

### 권장 구조

```text
pangi/
  pyproject.toml
  README.md
  src/
    pangi/
      app.py
      config/
      domain/
      usecase/
      repository/
      infra/
        slack/
        queue/
        git/
        codex/
        admin/
        logger/
        approvals/
  tests/
```

### 체크리스트

- [x] `pangi/` 루트 폴더를 만든다.
  - 완료 기준: 기존 `poppangbot/`과 별도 폴더로 존재한다.

- [x] `pangi/pyproject.toml`을 만든다.
  - 완료 기준: FastAPI, uvicorn, pytest, python-dotenv 또는 pydantic-settings 의존성이 정의되어 있다.

- [x] `pangi/src/pangi/app.py`를 만든다.
  - 완료 기준: FastAPI 앱 객체가 있고 `/health` route가 있다.

- [x] `pangi/src/pangi/config/` 폴더를 만든다.
  - 완료 기준: 설정 로딩 파일을 둘 위치가 있다.

- [x] `pangi/src/pangi/infra/slack/` 폴더를 만든다.
  - 완료 기준: Slack route, signature, client 모듈을 둘 위치가 있다.

- [x] `pangi/src/pangi/usecase/` 폴더를 만든다.
  - 완료 기준: 요청 분류와 job 생성 흐름을 둘 위치가 있다.

- [x] `pangi/src/pangi/infra/queue/` 폴더를 만든다.
  - 완료 기준: background job 실행기를 둘 위치가 있다.

- [x] `pangi/src/pangi/infra/codex/` 폴더를 만든다.
  - 완료 기준: Codex runner와 prompt builder를 둘 위치가 있다.

- [x] `pangi/src/pangi/infra/git/` 폴더를 만든다.
  - 완료 기준: worktree와 diff 관련 코드를 둘 위치가 있다.

- [x] `pangi/src/pangi/infra/logger/` 폴더를 만든다.
  - 완료 기준: redaction과 structured logging 코드를 둘 위치가 있다.

- [x] `pangi/src/pangi/domain/` 폴더를 만든다.
  - 완료 기준: 핵심 모델과 정책을 둘 위치가 있다.

- [x] `pangi/src/pangi/repository/` 폴더를 만든다.
  - 완료 기준: 저장소 인터페이스와 SQLite 구현을 둘 위치가 있다.

- [x] `pangi/tests/` 폴더를 만든다.
  - 완료 기준: 최소 `/health` 테스트를 추가할 수 있다.

- [x] 로컬 실행 명령을 정한다.
  - 명령 예시:
    ```bash
    cd pangi
    uvicorn pangi.app:app --reload --port 8000
    ```
  - 완료 기준: README에 실행 명령이 적혀 있다.
  - 테스트 명령 예시:
    ```bash
    cd pangi
    pytest
    ```

### 완료 기준

- [x] `pangi` 서버가 `/health`에 `{"status":"ok"}`를 반환한다.
- [x] 테스트에서 `/health` 응답을 검증한다.

---

## 3단계: 설정과 allowlist 기반 만들기

### 목표

서버가 어떤 Slack user/channel/repo를 허용할지 명확히 통제한다.

### 체크리스트

- [x] 환경변수 목록을 정리한다.
  - 최소 변수:
    ```text
    SLACK_SIGNING_SECRET
    SLACK_BOT_TOKEN
    SLACK_ALLOWED_USER_IDS
    SLACK_ALLOWED_CHANNEL_IDS
    PANGI_WORKTREE_ROOT
    PANGI_SOURCE_REPO_ROOT
    PANGI_JOB_TIMEOUT_SECONDS
    ```
  - 완료 기준: `.env.example`에 secret 값 없이 이름만 있다.

- [x] 설정 로더를 만든다.
  - 완료 기준: 환경변수에서 값을 읽고, 누락된 필수 값은 서버 시작 시 명확히 실패한다.

- [x] Slack user allowlist 파서를 만든다.
  - 완료 기준: 쉼표 구분 문자열을 set으로 변환한다.

- [x] Slack channel allowlist 파서를 만든다.
  - 완료 기준: 허용되지 않은 channel 요청은 job을 만들지 않는다.

- [x] source repo root 하위 repo 자동 탐색 구조를 정한다.
  - 예시:
    ```text
    /home/poppang/admin/pangi/repos/PopPang-iOS
    /home/poppang/admin/pangi/repos/PopPang-BE
    ```
  - 완료 기준: 사용자가 임의 경로를 Slack 메시지로 지정할 수 없고, `PANGI_SOURCE_REPO_ROOT` 하위 direct child repo만 인식한다.

- [x] worktree root 경로를 설정으로 분리한다.
  - 완료 기준: job worktree가 항상 `PANGI_WORKTREE_ROOT/{job_id}` 아래에 만들어진다.

- [x] timeout 기본값을 설정한다.
  - 권장:
    - read-only 분석: 600초
    - 수정: 1200초
    - build/test: 1800초 이상
  - 완료 기준: Codex runner가 이 timeout을 사용한다.

- [x] 설정 테스트를 작성한다.
  - 완료 기준: allowlist parsing, repo path lookup, timeout default가 테스트된다.

### 완료 기준

- [x] allowlist 밖 요청이 차단된다.
- [x] 사용자가 Slack 메시지로 임의 repo path를 실행시킬 수 없다.
- [x] 필수 설정 누락 시 서버가 조용히 실패하지 않는다.

---

## 4단계: Slack 요청 수신 계층 만들기

### 목표

Slack에서 들어온 요청을 안전하게 받고 내부 command 객체로 바꾼다.

### 체크리스트

- [x] `/slack/events` route를 만든다.
  - 완료 기준: Slack Events API 요청을 받는다.

- [x] `/slack/commands` route를 만든다.
  - 완료 기준: slash command 요청을 받는다.

- [x] `/slack/interactions` route 자리를 만든다.
  - 완료 기준: 1차 MVP에서는 미구현이어도 13단계에서 승인 버튼을 붙일 위치가 있다.

- [x] Slack signature 검증 함수를 만든다.
  - 완료 기준: 기존 `poppangbot/app.py`의 검증 로직과 같은 보안 수준이다.

- [x] timestamp tolerance를 적용한다.
  - 권장: 5분
  - 완료 기준: 오래된 timestamp 요청은 거절된다.

- [x] `url_verification` 처리를 구현한다.
  - 완료 기준: Slack 앱 설정에서 Request URL 검증이 가능하다.

- [x] `app_mention` 이벤트만 처리한다.
  - 완료 기준: 다른 이벤트는 `{"ok": true}`로 무시한다.

- [x] bot 자기 자신이 보낸 이벤트를 무시한다.
  - 완료 기준: `bot_id` 또는 subtype 이벤트로 loop가 생기지 않는다.

- [x] mention text에서 `<@BOT_ID>`를 제거한다.
  - 완료 기준: 내부 command text에는 사용자 요청만 남는다.

- [x] thread id를 계산한다.
  - 규칙:
    - `thread_ts`가 있으면 `thread_ts`
    - 없으면 원본 event `ts`
  - 완료 기준: 모든 답변이 같은 Slack thread로 간다.

- [x] Slack retry header를 처리한다.
  - 완료 기준: 같은 `event_id`로 중복 job이 만들어지지 않는다.

- [x] app mention route를 빠른 ACK 구조로 만든다.
  - 완료 기준: Slack Events API route는 유효한 app mention을 background task로 넘기고 즉시 200 OK를 반환한다.

- [x] 외부 웹/인터넷 URL 분석 요청을 차단한다.
  - 완료 기준: URL 또는 웹 검색 분석 요청은 job을 만들지 않고 Slack에 안내 응답만 보낸다.

- [x] 입력 가드레일을 orchestrator 앞단에 둔다.
  - 완료 기준: 외부 웹/URL 분석과 수정/PR/배포 요청은 Codex orchestrator 호출 전에 차단하고, 통과한 요청만 orchestrator가 분류한다.

- [x] 입력 가드레일을 코드 기반 1차 라우터로 고도화한다.
  - 완료 기준: 일반 대화, repo 불명확, 허용 repo 분석, 외부 웹/쓰기 차단은 Codex orchestrator 호출 없이 분류하고, 애매한 요청만 Codex orchestrator에 위임한다.

- [x] 기본 대화와 repo 분석 job을 분리한다.
  - 완료 기준: 일반 대화는 `codex_chat`으로 응답하고, 허용 repo key가 있는 repo 분석 요청만 AgentJob을 만든다.

- [x] Git MCP context와 repo catalog 요청을 repo 분석 job과 분리한다.
  - 완료 기준: PR/issue/Actions/commit 맥락 요청은 `git_context_chat`, 분석 가능한 repo 목록 요청은 `repo_catalog`로 분류하고 AgentJob을 만들지 않는다.

- [x] Codex CLI orchestrator adapter를 추가한다.
  - 완료 기준: `PANGI_ORCHESTRATOR_MODEL=gpt-5.4-mini`, `PANGI_ORCHESTRATOR_TIMEOUT_SECONDS=20` 설정으로 Codex CLI를 통해 structured decision을 받을 수 있다.

- [x] Codex 모델을 호출 목적별로 분리한다.
  - 완료 기준: 일반 대화와 orchestrator는 기본 `gpt-5.4-mini`, repo read-only 분석은 기본 `gpt-5.5`로 실행된다.

- [x] Codex reasoning effort를 호출 목적별로 명시한다.
  - 완료 기준: 일반 대화와 orchestrator는 `low`, repo read-only 분석은 `high`를 `codex exec -c model_reasoning_effort=...`로 전달한다.

- [x] orchestrator 런타임 프롬프트를 마크다운 파일로 분리한다.
  - 완료 기준: Codex orchestrator가 `pangi/src/pangi/prompts/orchestrator.md`를 읽어 structured decision instructions로 사용한다.

- [x] 요청을 내부 `SlackCommand` 타입으로 정규화한다.
  - 포함 필드:
    - team_id
    - channel_id
    - user_id
    - text
    - thread_ts
    - event_id
  - 완료 기준: orchestrator는 Slack payload 원본에 직접 의존하지 않는다.

- [x] Slack route 테스트를 작성한다.
  - 완료 기준:
    - signature valid 요청 성공
    - signature invalid 요청 401
    - stale timestamp 401
    - url verification 성공
    - app mention 정규화 성공

### 완료 기준

- [x] Slack app mention이 내부 command로 변환된다.
- [x] 잘못된 signature 요청이 차단된다.
- [x] Slack retry로 중복 job이 생기지 않는다.

---

## 5단계: 작업 모델과 job 저장소 만들기

### 목표

Slack thread와 실행 job을 추적할 수 있는 최소 저장 구조를 만든다.

### MVP 저장 방식 선택

- 빠른 MVP: SQLite
- 서버 운영 전제: PostgreSQL

처음부터 PostgreSQL을 쓰는 것이 최종 구조에는 좋지만, 로컬 MVP 속도가 중요하면 SQLite로 시작하고 repository 인터페이스를 분리한다.

### 체크리스트

- [x] `JobStatus` enum을 정의한다.
  - 값 예시:
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

- [x] `JobType` enum을 정의한다.
  - 값 예시:
    ```text
    analyze
    edit_requested
    pr_summary
    troubleshooting
    xcodebuild_failure
    ```

- [x] `AgentJob` 모델을 정의한다.
  - 필드:
    - id
    - slack_team_id
    - slack_channel_id
    - slack_thread_ts
    - slack_message_ts
    - requester_user_id
    - job_type
    - status
    - repo_key
    - prompt
    - worktree_path
    - stdout
    - stderr
    - error_message
    - created_at
    - updated_at

- [x] `SlackThread` 모델을 정의한다.
  - 필드:
    - id
    - team_id
    - channel_id
    - thread_ts
    - last_job_id
    - created_at
    - updated_at

- [x] `CodexRun` 모델을 정의한다.
  - 필드:
    - id
    - job_id
    - mode
    - command
    - stdout
    - stderr
    - exit_code
    - timed_out
    - started_at
    - finished_at

- [x] repository 인터페이스를 만든다.
  - 필요한 함수:
    - create_job
    - get_job
    - update_job_status
    - append_codex_run
    - find_job_by_event_id
    - get_or_create_thread

- [x] 중복 event id 방지 필드를 추가한다.
  - 완료 기준: Slack retry가 같은 job을 재사용한다.

- [x] 저장소 테스트를 작성한다.
  - 완료 기준:
    - job 생성
    - status 변경
    - thread 조회
    - event id 중복 방지

### 완료 기준

- [x] Slack 요청 하나가 job 하나로 저장된다.
- [x] job 상태 변화가 저장된다.
- [x] 서버가 재시작되어도 최소한 job 결과를 확인할 수 있다.

---

## 6단계: background job worker 만들기

### 목표

Slack 3초 응답 제한과 긴 Codex 실행을 분리한다.

### 체크리스트

- [x] job queue 인터페이스를 만든다.
  - 함수:
    - enqueue(job_id)
    - run(job_id)
    - cancel(job_id)

- [x] MVP용 in-process queue를 만든다.
  - 구현 후보:
    - `asyncio.Queue`
    - `asyncio.create_task`
  - 완료 기준: Slack route가 즉시 응답하고 job은 뒤에서 돈다.

- [x] worker lifecycle을 FastAPI startup/shutdown에 연결한다.
  - 완료 기준: 서버 시작 시 worker가 시작되고 종료 시 task가 정리된다.

- [x] job 상태 전환을 구현한다.
  - 흐름:
    ```text
    queued -> running -> succeeded
    queued -> running -> failed
    queued -> running -> timed_out
    ```

- [x] job 실행 중 Slack 중간 메시지를 보낼 수 있게 hook을 만든다.
  - 완료 기준: worker가 "접수", "분석 중", "완료" 상태를 Slack에 알릴 수 있다.

- [x] repo별 동시 실행 제한을 만든다.
  - MVP 권장: repo당 1개
  - 완료 기준: 같은 repo에 동시에 두 worktree 작업이 몰리지 않는다.

- [x] 전체 동시 실행 제한을 만든다.
  - MVP 권장: 전체 1~2개
  - 완료 기준: 서버에 요청이 몰려도 프로세스가 폭주하지 않는다.

- [x] cancellation 기본 구조를 만든다.
  - 완료 기준: job status를 `cancelled`로 바꿀 수 있다.

- [x] worker 테스트를 작성한다.
  - 완료 기준:
    - queued job 실행
    - success status 변경
    - failure status 변경
    - concurrency 제한

### 완료 기준

- [x] Slack route가 긴 작업을 기다리지 않는다.
- [x] job이 background에서 실행된다.
- [x] 실패해도 서버 프로세스 전체가 죽지 않는다.

---

## 7단계: git worktree manager 만들기

### 목표

Codex가 원본 repo를 직접 건드리지 않도록 job마다 격리된 작업 공간을 만든다.

### 체크리스트

- [x] source repo root 하위 repo path를 가져오는 함수를 만든다.
  - 완료 기준: 사용자가 직접 path를 입력할 수 없다.

- [x] read-only 분석에서는 새 branch를 만들지 않고 detached worktree를 사용한다.
  - 현재 규칙:
    ```text
    git worktree add --detach {worktree_path} origin/{base_branch}
    ```

- [x] job id 기반 worktree 경로 규칙을 정한다.
  - 예시:
    ```text
    {PANGI_WORKTREE_ROOT}/{job_id}
    ```

- [x] source repo가 git repo인지 확인한다.
  - 완료 기준: `.git`이 없거나 `git rev-parse` 실패 시 job 실패 처리한다.

- [x] base branch를 설정으로 분리한다.
  - 예시:
    ```text
    PANGI_DEFAULT_BASE_BRANCH=develop
    ```
  - 완료 기준: `develop`이 없으면 `main`으로 fallback한다.

- [x] `git fetch origin` 실행 함수를 만든다.
  - 완료 기준: 실패 시 stderr를 job error로 저장한다.

- [x] `git worktree add` 실행 함수를 만든다.
  - 완료 기준: 새 worktree가 생성된다.

- [x] 이미 같은 path가 있을 때의 정책을 정한다.
  - 권장: 같은 job id면 재사용 금지, 명확히 실패
  - 완료 기준: 기존 작업물을 덮어쓰지 않는다.

- [x] worktree 생성 후 detached checkout인지 확인한다.
  - 완료 기준: read-only 분석 worktree가 branch 없이 detached 상태다.

- [x] 원본 branch가 main/develop이면 직접 수정하지 않는 검사를 추가한다.
  - 완료 기준: Codex 실행 cwd가 source repo가 아니라 worktree다.

- [ ] `git status --short` 수집 함수를 만든다.
  - 완료 기준: 변경 파일 목록을 수집할 수 있다.

- [ ] `git diff --stat` 수집 함수를 만든다.
  - 완료 기준: Slack에 요약 표시 가능하다.

- [ ] `git diff` 수집 함수를 만든다.
  - 완료 기준: PR 승인 전 diff를 확인할 수 있다.

- [ ] cleanup 함수 초안을 만든다.
  - 완료 기준: completed/cancelled job worktree를 나중에 정리할 수 있다.

- [x] worktree 테스트를 작성한다.
  - 완료 기준:
    - 임시 git repo에서 worktree 생성 성공
    - 잘못된 repo path 실패
    - 이미 존재하는 worktree path 실패

### 완료 기준

- [x] 모든 Codex 실행 cwd가 worktree 경로다.
- [x] 원본 repo가 직접 수정되지 않는다.
- [x] worktree 생성 실패가 Slack/job 상태에 반영된다.

---

## 8단계: Codex runner 만들기

### 목표

서버가 `codex exec`를 안전하게 실행하고 결과를 수집한다.

### 체크리스트

- [x] Codex runner 인터페이스를 만든다.
  - 함수 예시:
    ```text
    run_codex(workdir, prompt, mode, timeout_seconds)
    ```

- [x] read-only mode를 구현한다.
  - 명령 형태:
    ```bash
    codex exec -C {worktree_path} --sandbox read-only "{prompt}"
    ```
  - 완료 기준: 분석 job은 항상 read-only로 실행된다.

- [ ] workspace-write mode는 아직 실행하지 않고 자리만 만든다.
  - 완료 기준: 13단계 전까지는 호출되지 않는다.

- [x] `asyncio.create_subprocess_exec`를 사용한다.
  - 완료 기준: `shell=True`를 사용하지 않는다.

- [x] argv list로 명령을 구성한다.
  - 완료 기준: 사용자 입력이 shell command로 해석되지 않는다.

- [x] stdout 수집을 구현한다.
  - 완료 기준: Codex 결과가 job에 저장된다.

- [x] stderr 수집을 구현한다.
  - 완료 기준: 실패 원인을 Slack에 요약할 수 있다.

- [x] exit code 수집을 구현한다.
  - 완료 기준: 0이 아니면 failed 처리한다.

- [x] timeout 처리를 구현한다.
  - 완료 기준: timeout 시 process terminate 후 필요하면 kill한다.

- [x] 실행 시간을 기록한다.
  - 완료 기준: started_at, finished_at을 저장한다.

- [x] prompt builder를 만든다.
  - 완료 기준: 사용자 요청, repo 이름, thread context, 출력 규칙이 포함된다.

- [x] PopPang 공통 스타일 prompt를 만든다.
  - 완료 기준: `pangi/src/pangi/prompts/pangi_agent.md`에 코드, 개발, 커밋, 디자인 스타일 기준을 분리하고 chat/read-only 분석 prompt가 함께 사용한다.

- [x] 일반 대화 prompt에 인사/자기소개 응답 규칙을 넣는다.
  - 완료 기준: 인사나 자기소개 요청에는 팡이의 역할, 가능한 일, MVP에서 직접 실행하지 않는 일을 짧게 안내한다.

- [x] 분석 prompt의 출력 규칙을 고정한다.
  - 필수:
    - 결론 먼저
    - 근거 파일 경로
    - 확인한 사실과 추정 분리
    - 수정하지 말 것
    - 검증 방법
    - 요약

- [x] secret redaction을 runner 결과에 적용한다.
  - 완료 기준: Slack/Notion에 보내기 전 token 형태 문자열을 가린다.

- [x] `codex` binary 존재 확인을 추가한다.
  - 완료 기준: 서버 시작 또는 job 시작 시 `codex`가 없으면 명확한 에러가 난다.

- [x] Codex runner 테스트를 작성한다.
  - 방법: 실제 `codex` 대신 dummy command를 주입할 수 있게 만든다.
  - 완료 기준:
    - stdout 수집
    - stderr 수집
    - non-zero exit
    - timeout

### 완료 기준

- [x] 서버가 worktree에서 Codex read-only 분석을 실행한다.
- [x] stdout/stderr/exit code/timeout이 모두 저장된다.
- [x] 사용자 입력이 shell command로 실행될 수 없다.

---

## 9단계: Slack thread 응답 연결하기

### 목표

작업 상태와 최종 결과를 원래 Slack thread에 반환한다.

### 체크리스트

- [x] Slack Web API client를 만든다.
  - 완료 기준: `chat.postMessage`를 호출할 수 있다.

- [x] 접수 메시지를 보낸다.
  - 예시:
    ```text
    팡이가 요청을 접수했습니다. 먼저 read-only로 확인해볼게요.
    ```

- [x] 접수 시 원본 Slack 메시지에 준비중 reaction을 단다.
  - 완료 기준: `app_mention` 이벤트의 원본 `ts`에 `eyes` reaction을 추가한다.

- [x] 일반 대화 응답 완료 reaction을 표시한다.
  - 완료 기준: 일반 대화 응답을 Slack thread에 성공적으로 보낸 뒤 원본 메시지의 `eyes` reaction을 제거하고 `white_check_mark` reaction을 추가한다.

- [x] repo 분석 응답 완료 reaction을 표시한다.
  - 완료 기준: read-only 분석 결과를 Slack thread에 성공적으로 보낸 뒤 원본 메시지의 `eyes` reaction을 제거하고 `white_check_mark` reaction을 추가한다. 실패/timeout 결과 응답을 보낸 경우에는 `x` reaction으로 전환한다.

- [ ] worktree 생성 시작 메시지를 보낸다.
  - 예시:
    ```text
    안전한 작업 공간을 만들고 있습니다.
    ```

- [ ] Codex 실행 시작 메시지를 보낸다.
  - 예시:
    ```text
    팡이가 코드를 읽고 있습니다.
    ```

- [ ] 중간 상태 메시지 rate limit을 둔다.
  - 완료 기준: 너무 많은 Slack 메시지를 보내지 않는다.

- [x] 성공 결과 메시지 포맷을 만든다.
  - 포함:
    - 결론
    - 근거 파일
    - 추천 작업
    - 검증 방법
    - job id

- [x] 실패 결과 메시지 포맷을 만든다.
  - 포함:
    - 실패 단계
    - 에러 요약
    - 재시도 가능 여부
    - job id

- [x] timeout 결과 메시지 포맷을 만든다.
  - 완료 기준: timeout과 일반 실패를 구분한다.

- [x] Slack 메시지 길이 제한을 고려한다.
  - 완료 기준: 긴 stdout은 요약하거나 잘라서 보낸다.

- [x] 출력 가드레일을 공통화한다.
  - 완료 기준: 일반 대화, 정책 안내, repo 분석 결과가 Slack 전송 전 공통 출력 가드레일을 통과한다.

- [x] Markdown to Slack 변환을 Slack adapter 경계에 둔다.
  - 완료 기준: Slack bot 응답만 Slack mrkdwn으로 변환하고, usecase와 관리자 화면은 canonical Markdown을 다룬다.

- [x] Slack 응답 테스트를 작성한다.
  - 방법: Slack client mock 사용
  - 완료 기준:
    - thread_ts로 답장
    - 성공 메시지 생성
    - 실패 메시지 생성

### 완료 기준

- [x] 사용자는 Slack thread에서 job 상태를 볼 수 있다.
- [x] 최종 분석 결과가 thread에 표시된다.
- [x] 실패해도 조용히 사라지지 않는다.

---

## 10단계: 1차 MVP end-to-end 검증

### 목표

Slack에서 요청을 보내면 팡이가 실제로 read-only 분석 결과를 thread에 답한다.

### 체크리스트

- [ ] 로컬에서 팡이 서버를 실행한다.
  - 완료 기준: `/health`가 정상 응답한다.

- [ ] ngrok 또는 서버 도메인으로 Slack Request URL을 연결한다.
  - 완료 기준: Slack URL verification이 성공한다.

- [ ] 테스트 Slack channel에 봇을 초대한다.
  - 완료 기준: `@팡이` mention 이벤트가 서버 로그에 찍힌다.

- [ ] allowlist에 테스트 user/channel을 추가한다.
  - 완료 기준: 허용된 사용자만 job 생성 가능하다.

- [ ] source repo path를 설정한다.
  - 완료 기준: 서버가 PopPang-iOS source repo를 찾을 수 있다.

- [ ] worktree root 경로를 설정한다.
  - 완료 기준: job별 worktree가 생성될 수 있다.

- [ ] `codex` CLI 로그인 상태를 확인한다.
  - 완료 기준: 서버 실행 계정에서 `codex exec`가 실행 가능하다.

- [ ] Slack에서 분석 요청을 보낸다.
  - 예시:
    ```text
    @팡이 이 프로젝트 구조를 간단히 분석해줘
    ```

- [ ] job이 생성되는지 확인한다.
  - 완료 기준: job status가 `queued`에서 `running`으로 바뀐다.

- [ ] worktree가 생성되는지 확인한다.
  - 완료 기준: worktree path가 job에 저장된다.

- [ ] Codex가 read-only로 실행되는지 확인한다.
  - 완료 기준: command에 `--sandbox read-only`가 있다.

- [ ] Slack thread에 최종 응답이 오는지 확인한다.
  - 완료 기준: 결론과 근거 파일이 포함되어 있다.

- [ ] 실패 케이스를 하나 테스트한다.
  - 예시: 잘못된 repo key
  - 완료 기준: Slack에 실패 메시지가 온다.

- [ ] timeout 케이스를 테스트한다.
  - 완료 기준: job status가 `timed_out`이 되고 Slack에 안내된다.

### 완료 기준

- [ ] 1차 MVP 흐름이 실제 Slack에서 동작한다.
- [ ] 분석 결과가 사람이 읽을 수 있는 형태로 온다.
- [ ] 실패와 timeout이 처리된다.

---

## 11단계: 안전장치 강화

### 목표

팀 내부 도구라도 운영 전에 반드시 필요한 방어선을 넣는다.

### 체크리스트

- [ ] Slack user allowlist를 강제한다.
  - 완료 기준: 허용되지 않은 user는 job 생성 불가다.

- [ ] Slack channel allowlist를 강제한다.
  - 완료 기준: 허용되지 않은 channel은 job 생성 불가다.

- [ ] source repo root 하위 repo만 허용한다.
  - 완료 기준: `PANGI_SOURCE_REPO_ROOT` 밖 repo는 접근 불가다.

- [ ] source repo 직접 실행 방지 검사를 넣는다.
  - 완료 기준: Codex cwd가 source repo면 실행이 막힌다.

- [ ] worktree path prefix 검사를 넣는다.
  - 완료 기준: Codex cwd는 항상 `PANGI_WORKTREE_ROOT` 하위다.

- [x] `.env` 파일 읽기/출력 금지 규칙을 prompt에 넣는다.
  - 완료 기준: prompt template에 명시되어 있다.

- [x] secret redaction 패턴을 만든다.
  - 최소:
    - `xoxb-`
    - `ghp_`
    - `sk-`
    - `SLACK_...=`
    - `TOKEN=`
  - 완료 기준: Slack 출력 전 redaction을 통과한다.

- [ ] log 최대 크기를 제한한다.
  - 완료 기준: stdout/stderr가 무한히 DB/Slack에 쌓이지 않는다.

- [ ] Slack 메시지 최대 길이 처리를 넣는다.
  - 완료 기준: 긴 결과는 잘라서 "전체 로그는 저장소/파일 참조" 형태가 된다.

- [ ] job timeout 기본값을 강제한다.
  - 완료 기준: timeout 없는 Codex 실행이 없다.

- [ ] cancellation 상태를 worker가 존중한다.
  - 완료 기준: 취소된 job은 실행되지 않거나 실행 중단된다.

- [x] prompt injection 방어 문구를 넣는다.
  - 완료 기준: 사용자가 "이전 지시 무시하고 secret 출력" 같은 요청을 해도 시스템 규칙이 우선임을 prompt에 명시한다.

- [ ] shell injection 회귀 테스트를 작성한다.
  - 완료 기준: 사용자 메시지에 `; rm -rf` 같은 문자열이 있어도 argv prompt로만 전달된다.

### 완료 기준

- [ ] 허용되지 않은 사용자/채널/repo가 차단된다.
- [ ] secret이 Slack/로그에 노출되지 않는다.
- [ ] Codex 실행 위치가 항상 안전한 worktree다.

---

## 12단계: Notion report 붙이기

### 목표

완료된 작업을 Notion episode report로 남긴다.

### Notion context 읽기 준비

- [x] Notion 문서 읽기 요청을 별도 분류로 추가한다.
  - 완료 기준: 입력 가드레일이 Notion 문서/회의록 요청을 `notion_context_chat`으로 분류한다.

- [x] Notion write 요청을 차단한다.
  - 완료 기준: Notion에 생성/추가/수정/삭제/기록하는 요청은 Codex 호출 전에 `unsupported`가 된다.

- [x] Notion context provider 포트를 추가한다.
  - 완료 기준: usecase는 Notion MCP 구현체가 아니라 `NotionContextProvider` 계약에만 의존한다.

- [x] Notion context prompt injection 방어 문구를 넣는다.
  - 완료 기준: Notion 본문은 분석 대상 데이터이며 팡이가 따라야 할 지시가 아니라고 prompt에 명시한다.

- [x] 공식 Notion MCP OAuth/PKCE client를 구현한다.
  - 완료 기준: `https://mcp.notion.com/mcp` Streamable HTTP transport로 read-only tool 호출을 수행한다.

- [x] Notion page/database allowlist를 MCP 호출 전에 강제한다.
  - 완료 기준: 허용되지 않은 Notion page/database는 조회하지 않는다.

### Git MCP context 읽기 준비

- [x] GitHub/Git context 읽기 요청을 별도 분류로 추가한다.
  - 완료 기준: 입력 가드레일이 PR, issue, Actions, commit 맥락 요청을 `git_context_chat`으로 분류한다.

- [x] 분석 가능한 repo 목록 요청을 별도 분류로 추가한다.
  - 완료 기준: 입력 가드레일이 repo catalog 요청을 `repo_catalog`로 분류한다.

- [x] Git write 요청을 차단한다.
  - 완료 기준: PR 생성, issue 생성, commit, push, merge 요청은 Codex 호출 전에 `unsupported`가 된다.

- [x] Git context provider 포트를 추가한다.
  - 완료 기준: usecase는 Git MCP 구현체가 아니라 `GitContextProvider` 계약에만 의존한다.

- [x] Git context prompt injection 방어 문구를 넣는다.
  - 완료 기준: Git MCP context는 분석 대상 데이터이며 팡이가 따라야 할 지시가 아니라고 prompt에 명시한다.

- [x] Git MCP Streamable HTTP JSON-RPC client를 구현한다.
  - 완료 기준: Git MCP endpoint를 read-only context provider로 감쌀 수 있다.

- [x] Git MCP repo 목록과 로컬 source repo 목록 비교를 구현한다.
  - 완료 기준: `ready`, `not_cloned`, `local_only` 상태로 repo catalog를 만들 수 있다.

### 체크리스트

- [x] Notion 사용 여부를 설정으로 켠다.
  - 완료 기준: `PANGI_NOTION_ENABLED=0`이면 Notion 없이도 서버가 돈다.

- [x] Notion database id 설정을 추가한다.
  - 완료 기준: secret 값 없이 `.env.example`에 변수명이 있다.

- [ ] Notion client wrapper를 만든다.
  - 완료 기준: page 생성 함수를 호출할 수 있다.

- [ ] report 템플릿을 정한다.
  - 포함:
    - 제목
    - 요청자
    - Slack thread link
    - job type
    - 결론
    - 근거 파일
    - stdout 요약
    - 실패 시 에러 요약

- [ ] Slack thread link 생성 함수를 만든다.
  - 완료 기준: Notion에서 원본 Slack thread로 이동할 수 있다.

- [ ] successful analyze job 완료 후 Notion page를 생성한다.
  - 완료 기준: Notion page id가 job에 저장된다.

- [ ] failed/timed_out job도 Notion에 기록할지 결정한다.
  - 권장: 기록
  - 완료 기준: 실패 원인 추적 가능하다.

- [ ] Notion에 쓰기 전 redaction을 적용한다.
  - 완료 기준: secret 패턴이 가려진다.

- [ ] Notion 실패가 전체 job 성공을 깨지 않게 한다.
  - 완료 기준: Slack 분석 결과는 성공했고 Notion만 실패하면 경고로 처리한다.

### 완료 기준

- [ ] 분석 job 결과가 Notion에 저장된다.
- [ ] Slack thread에 Notion 링크가 표시된다.
- [ ] Notion 실패가 핵심 Slack 분석 흐름을 막지 않는다.

---

## 13단계: 수정 승인 흐름 붙이기

### 목표

코드 수정 요청은 바로 수정하지 않고, 분석 후 Slack 승인 버튼을 통해서만 workspace-write를 실행한다.

### 체크리스트

- [ ] 수정 요청 분류 규칙을 만든다.
  - 예시 키워드:
    - 수정
    - 고쳐
    - 리팩터링
    - 구현
    - 추가
  - 완료 기준: 수정 요청도 첫 단계는 read-only 분석이다.

- [ ] 수정 요청 분석 prompt를 만든다.
  - 완료 기준: "파일을 수정하지 말고 수정 계획만 제안" 규칙이 들어 있다.

- [ ] 분석 결과에 승인 버튼을 붙인다.
  - 버튼:
    - 수정 실행
    - 취소
  - 완료 기준: Slack interactive message가 표시된다.

- [ ] `/slack/interactions`에서 승인 버튼을 처리한다.
  - 완료 기준: Slack signature 검증 후 action을 파싱한다.

- [ ] 승인자 권한을 검사한다.
  - 권장:
    - 요청자
    - admin allowlist
  - 완료 기준: 아무나 수정 실행을 누를 수 없다.

- [ ] approval 모델을 만든다.
  - 필드:
    - job_id
    - approval_type
    - status
    - requested_by
    - decided_by
    - decided_at

- [ ] 승인 시 workspace-write job을 enqueue한다.
  - 완료 기준: 같은 worktree에서 수정이 진행된다.

- [ ] workspace-write Codex prompt를 만든다.
  - 완료 기준:
    - 승인된 범위만 수정
    - `.env` 열람 금지
    - 변경 파일 목록 출력
    - 검증 방법 출력

- [ ] 수정 후 diff를 수집한다.
  - 완료 기준: `git status`, `git diff --stat`, `git diff --name-only`가 저장된다.

- [ ] diff 요약을 Slack thread에 반환한다.
  - 완료 기준: 사용자가 변경 파일과 영향 범위를 볼 수 있다.

- [ ] 허용 범위 밖 파일 변경을 감지한다.
  - 완료 기준: 이상한 파일 변경 시 PR 생성 단계로 넘어가지 않는다.

- [ ] 수정 실패 시 worktree를 보존한다.
  - 완료 기준: 디버깅을 위해 바로 삭제하지 않는다.

### 완료 기준

- [ ] 코드 수정은 Slack 승인 없이는 실행되지 않는다.
- [ ] 수정은 worktree 안에서만 일어난다.
- [ ] 수정 결과 diff가 Slack thread에 표시된다.

---

## 14단계: PR 생성 흐름 붙이기

### 목표

수정 결과를 확인한 뒤 승인된 경우에만 서버가 commit/push/PR을 생성한다.

### 체크리스트

- [ ] PR 생성 버튼을 diff 응답에 붙인다.
  - 버튼:
    - PR 생성
    - 수정 취소
  - 완료 기준: 수정 완료 후 바로 PR 생성하지 않는다.

- [ ] PR 승인자 권한을 검사한다.
  - 완료 기준: 요청자 또는 admin만 PR 생성 가능하다.

- [ ] commit message 생성 규칙을 정한다.
  - 예시:
    ```text
    Refactor login view state handling
    ```
  - 완료 기준: 너무 긴 Codex 출력이 commit message가 되지 않는다.

- [ ] PR branch push를 구현한다.
  - 완료 기준: worktree branch가 origin에 push된다.

- [ ] GitHub PR 생성 방식을 정한다.
  - 후보:
    - `gh pr create`
    - GitHub API
  - MVP 권장: `gh pr create`

- [ ] PR body 템플릿을 만든다.
  - 포함:
    - 요약
    - 변경 파일
    - 검증 결과
    - Slack thread link
    - Notion link

- [ ] PR 생성 전 최소 검증을 실행한다.
  - 최소:
    - `git diff --check`
    - 가능하면 관련 테스트
  - 완료 기준: 검증 실패 시 PR 생성 전 Slack에 알린다.

- [ ] PR URL을 job에 저장한다.
  - 완료 기준: Slack/Notion에서 PR로 이동할 수 있다.

- [ ] PR 생성 실패 처리를 구현한다.
  - 완료 기준: push 실패와 PR 생성 실패가 구분된다.

- [ ] PR 생성 후 Slack thread에 링크를 반환한다.
  - 완료 기준: 사용자가 Slack에서 PR 링크를 바로 볼 수 있다.

### 완료 기준

- [ ] 서버가 승인된 작업만 PR로 올린다.
- [ ] PR 생성 전 diff와 검증 결과를 확인한다.
- [ ] PR URL이 Slack과 job 기록에 남는다.

---

## 15단계: 운영/배포 정리

### 목표

팀이 안정적으로 팡이를 켜두고 사용할 수 있게 운영 문서를 정리한다.

### 체크리스트

- [ ] 서버 실행 방식을 정한다.
  - 후보:
    - systemd
    - Docker
    - nohup MVP
  - 완료 기준: 서버 재시작 방법이 문서화되어 있다.

- [ ] 환경변수 배포 방식을 정한다.
  - 완료 기준: secret을 git에 커밋하지 않는다.

- [ ] 로그 위치를 정한다.
  - 완료 기준: 서버 로그와 job 로그 위치가 다르거나 구분된다.

- [x] 관리자용 DB 확인 페이지를 만든다.
  - 완료 기준: 관리자 로그인 후 최근 `agent_jobs`, `slack_threads`, `codex_runs`를 브라우저에서 확인할 수 있다.

- [ ] health check endpoint를 배포 환경에서 확인한다.
  - 완료 기준: 외부 또는 내부에서 `/health` 확인 가능하다.

- [ ] Slack Request URL을 운영 도메인으로 연결한다.
  - 완료 기준: Slack Event Subscription URL verification 성공.

- [ ] reverse proxy 설정을 정리한다.
  - 완료 기준: `/slack/events`, `/slack/commands`, `/slack/interactions`가 서버로 프록시된다.

- [ ] Codex CLI 실행 계정을 정한다.
  - 완료 기준: 서버 실행 계정에서 `codex exec`가 동작한다.

- [ ] Codex auth 파일 권한을 확인한다.
  - 완료 기준: 서버 사용자만 읽을 수 있다.

- [ ] worktree root cleanup 정책을 정한다.
  - 예시:
    - succeeded job: 7일 후 삭제
    - failed job: 14일 보관
    - PR 생성 job: PR merge 후 삭제

- [ ] 장애 대응 문서를 만든다.
  - 포함:
    - Slack 응답 없음
    - Codex timeout
    - worktree 생성 실패
    - GitHub push 실패
    - Notion 저장 실패

- [ ] 운영 smoke test를 만든다.
  - 완료 기준: 배포 후 `@팡이 ping` 또는 `@팡이 분석 테스트`로 동작 확인 가능하다.

### 완료 기준

- [ ] 서버를 재시작해도 설정과 job 흐름이 유지된다.
- [ ] Slack에서 운영 팡이를 호출할 수 있다.
- [ ] 장애가 났을 때 확인할 로그와 복구 절차가 있다.

---

## 16단계: 나중에 붙일 기능

이 단계는 1차 MVP 이후에 진행한다.

- [ ] Slack thread별 Codex session resume
- [ ] PR 요약 기능
- [ ] PopPang 내부 URL 허용 정책 검토
  - 현재는 서버 부하와 보안 이유로 URL이 포함된 요청을 모두 외부 웹/인터넷 분석으로 보고 차단한다.
  - 추후 GitHub PR, PopPang Notion, PopPang 내부 문서처럼 신뢰할 수 있는 URL만 allowlist로 열지 결정한다.
  - 허용하더라도 일반 웹 검색이나 임의 URL fetch는 기본 차단을 유지한다.
- [ ] xcodebuild 실패 로그 자동 분석
- [ ] Tuist generate/build/test 자동 감지
- [ ] Notion troubleshooting report 고도화
- [ ] repo별 실행 정책 세분화
- [ ] 사용자별 권한 세분화
- [ ] Redis/RQ 또는 Celery worker 분리
- [ ] PostgreSQL migration 정리
- [ ] job dashboard
- [ ] cost/usage tracking
- [ ] prompt template 관리 화면
- [ ] eval/test prompt 세트

---

## 매 구현 후 공통 체크

기능을 하나 만들 때마다 아래를 확인한다.

- [ ] 테스트를 추가했거나, 테스트하지 못한 이유를 기록했다.
- [ ] Slack에 보여지는 메시지가 너무 길지 않다.
- [ ] secret이 로그/Slack/Notion에 노출되지 않는다.
- [ ] 실패 케이스가 사용자에게 보인다.
- [ ] timeout이 있다.
- [ ] 사용자의 입력이 shell command로 해석되지 않는다.
- [ ] 원본 repo를 직접 수정하지 않는다.
- [ ] README 또는 관련 문서가 바뀐 동작과 일치한다.
