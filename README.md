# Pangi

팡이는 PopPang 팀 전용 Slack 기반 개발 에이전트입니다.

Slack에서 팀원이 `@팡이`에게 요청하면, 팡이가 먼저 코드와 맥락을 확인하고 팀이 더 빠르게 판단할 수 있도록 분석 결과를 Slack thread에 돌려주는 것을 목표로 합니다.

```text
팡이가 먼저 보고, 팀이 더 빠르게 결정합니다.
```

## 지금 이 저장소의 상태

현재 저장소에는 세 가지가 있습니다.

```text
poppangbot/  Slack 연결을 검증하기 위한 FastAPI 샘플 봇
pangi/       실제 팡이 MVP 본체를 구현할 Python/FastAPI 패키지
docs/        팡이 MVP 설계와 구현 체크리스트
```

중요한 점은 `poppangbot/`이 완성된 팡이 플랫폼이 아니라는 것입니다. 지금은 Slack slash command와 app mention이 서버까지 잘 도착하는지 확인하기 위한 샘플입니다.

실제 팡이 플랫폼은 `poppangbot/` 샘플과 분리된 별도 `pangi/` 패키지에 Python/FastAPI 기반으로 구현합니다.

## 팡이가 하려는 일

팡이의 첫 목표는 거창한 자동 개발 플랫폼이 아닙니다.

처음에는 아래 흐름만 안정적으로 만드는 것이 핵심입니다.

```text
Slack 요청
-> FastAPI 서버 수신
-> 요청 종류 판단
-> git worktree 생성
-> codex exec read-only 실행
-> 결과를 Slack thread에 응답
```

즉, 1차 MVP에서는 팡이가 코드를 바로 고치지 않습니다.

먼저 읽고, 분석하고, 근거를 보여주는 역할에 집중합니다.

## 왜 Python 기반인가

이미 `poppangbot/`이 Python/FastAPI로 만들어져 있습니다.

그래서 MVP 단계에서는 Python을 유지하는 편이 단순합니다.

- Slack Webhook 서버를 빠르게 만들 수 있습니다.
- `codex exec`, `git`, `xcodebuild` 같은 외부 명령 실행을 다루기 좋습니다.
- 기존 샘플의 Slack 서명 검증과 테스트 구조를 재사용할 수 있습니다.
- 운영 스크립트와 서버 로직을 같은 언어로 묶기 쉽습니다.

TypeScript가 나쁜 선택이라는 뜻은 아닙니다. 다만 지금 PopPang 규모에서는 Python으로 시작하는 쪽이 더 빠르고 현실적입니다.

## MVP 아키텍처

PopPang 규모에 맞춘 현실적인 MVP 구조는 아래 정도면 충분합니다.

```text
Slack
-> Webhook Server
-> Orchestrator
-> Job Worker
-> Worktree Manager
-> Codex Runner
-> Slack Thread Reply
```

각 역할은 단순하게 가져갑니다.

- Slack: 팀원이 팡이를 부르는 입구
- Webhook Server: Slack 요청을 받고 검증하는 FastAPI 서버
- Orchestrator: 분석 요청인지, 수정 요청인지, PR 요청인지 판단
- Job Worker: 오래 걸리는 작업을 Slack 응답과 분리해서 실행
- Worktree Manager: 원본 repo를 직접 건드리지 않도록 작업 공간 생성
- Codex Runner: `codex exec` 실행과 stdout/stderr 수집
- Slack Thread Reply: 결과를 원래 요청 thread에 반환

## 코드 수정은 나중 단계

팡이가 코드를 수정하는 흐름은 반드시 승인 기반으로 가야 합니다.

권장 흐름:

```text
read-only 분석
-> Slack에서 수정 승인
-> workspace-write 실행
-> diff 반환
-> Slack에서 PR 승인
-> 서버가 commit/push/PR 생성
```

Codex가 바로 원본 repo를 수정하거나, 바로 commit/push/PR까지 하게 두지 않습니다.

서버가 작업 상태와 승인 흐름을 통제하는 구조가 안전합니다.

## 안전 규칙

MVP라도 아래 규칙은 처음부터 넣는 편이 좋습니다.

- 허용된 Slack user만 실행
- 허용된 Slack channel만 실행
- 허용된 repo만 접근
- 원본 repo 직접 수정 금지
- job마다 git worktree 생성
- 분석은 `--sandbox read-only`
- 수정은 승인 후 `--sandbox workspace-write`
- Slack/Notion 로그에 secret 출력 금지
- 사용자의 메시지를 shell command로 직접 실행 금지
- timeout과 취소 처리

## 참고하고 싶은 사례에서 가져올 것

아래 사례들은 그대로 복사하기보다 원칙만 가져옵니다.

- AB180 에이봇: Slack에서 요청을 받고 Orchestrator가 필요한 작업을 나누는 구조
- 당근 카비: Slack을 팀의 업무 맥락이 모이는 인터페이스로 사용하는 방식
- 당근 GenAI 플랫폼: 실패, retry, fallback 같은 운영 안정성 관점
- 레거시 코드베이스 사례: 사용자의 팀, 권한, 접근 가능한 repo에 맞춰 응답하는 방식

PopPang MVP에서는 이 원칙을 작게 줄여서 적용합니다.

처음부터 서브에이전트, 스케줄러, Eval 플랫폼, 모델 라우터까지 만들 필요는 없습니다.

## 1차 구현 목표

가장 작은 성공 기준은 이것입니다.

1. Slack에서 `@팡이 분석해줘`라고 말한다.
2. FastAPI 서버가 요청을 받는다.
3. 서버가 작업을 background job으로 넘긴다.
4. 서버가 PopPang-iOS worktree를 만든다.
5. 서버가 `codex exec --sandbox read-only`를 실행한다.
6. 팡이가 분석 결과를 Slack thread에 답한다.

이 흐름이 안정적으로 돌면 그 다음에 Notion 기록, 수정 승인, PR 생성을 붙입니다.

## 현재 구현된 것

`pangi/` 본체에는 현재 아래 기능이 구현되어 있습니다.

- FastAPI 앱과 `/health` 상태 확인
- `pangi/.env`와 `.env.example` 기반 설정 로딩
- Slack user/channel allowlist 파싱
- repo allowlist와 worktree root 설정 파싱
- Slack request signature 검증
- Slack timestamp 5분 tolerance 검증
- `/slack/events` route
- Slack `url_verification` challenge 응답
- Slack `app_mention` 이벤트 정규화
- bot이 보낸 Slack 이벤트 무시
- Slack retry 중복 감지
- `/slack/commands` route
- `/slack/interactions` placeholder
- 내부 `SlackCommand` 타입 변환
- `SlackThread`, `AgentJob`, `CodexRun` 모델
- SQLite 기반 job 저장소
- Slack 요청을 `queued` 상태 job으로 저장
- Slack event id 기반 중복 job 방지
- job 상태 변경과 Codex run 기록 저장
- in-process background worker
- FastAPI startup/shutdown 기반 worker lifecycle
- job 상태 전환: `queued` -> `running` -> `succeeded` / `failed` / `timed_out`
- worker progress hook
- 전체/repo별 동시 실행 제한
- job cancellation 기본 구조
- read-only 분석용 git worktree 생성
- `codex exec --sandbox read-only` 실행
- Codex stdout/stderr/exit code/timeout 저장
- Slack thread에 read-only 분석 성공/실패/timeout 결과 응답
- Slack Web API `chat.postMessage` 기반 접수/상태 메시지
- Slack Web API `reactions.add` 기반 요청 접수 `eyes` reaction
- 관리자 로그인 페이지
- 관리자용 SQLite DB 확인 페이지 `/pangi-admin/db`

현재 Slack 요청을 받으면 검증과 정규화를 수행한 뒤 SQLite에 job으로 저장하고 background worker에 넘깁니다. worker는 허용된 source repo에서 read-only 분석용 detached worktree를 만들고, 그 worktree에서 Codex read-only 분석을 실행한 뒤 결과를 Slack thread에 반환합니다.

## 아직 구현되지 않은 것

아래 기능은 아직 구현되지 않았습니다.

- 실제 Slack 환경에서의 1차 MVP end-to-end 검증
- worktree cleanup 정책
- PR 승인 전 diff 수집/검토 흐름
- Notion 기록
- 코드 수정 승인 흐름
- PR 생성 흐름

## 문서

자세한 설계와 구현 기준은 아래 문서에 있습니다.

- [에이전트 작업 지침](AGENTS.md)
- [팡이 MVP 개요](docs/mvp/overview.md)
- [팡이 구현 체크리스트](docs/implementation-checklist.md)
- [팡이 안전 규칙](docs/security/safety-rules.md)
- [긴 설계 초안 보관본](docs/reference/pangi-platform-design-python.md)
- [poppangbot Slack 샘플 봇 README](poppangbot/README.md)

긴 설계 초안은 참고용입니다. 실제 구현할 때는 `AGENTS.md`의 문서 라우팅을 따라 필요한 문서만 먼저 읽는 것을 권장합니다.

## 로컬 샘플 봇 실행

Slack 연결 샘플을 실행하려면:

```bash
cd poppangbot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
```

상태 확인:

```bash
curl http://127.0.0.1:8000/health
```

Slack 설정 방법은 [poppangbot/README.md](poppangbot/README.md)를 참고합니다.

## 팡이 서버 뼈대 실행

실제 MVP 본체는 `pangi/` 패키지에서 시작합니다.

가상환경은 저장소 루트가 아니라 `pangi/.venv`에 둡니다. 이미 루트 `.venv`를 만들었다면 옮기기보다 `pangi/.venv`를 새로 만들고 의존성을 다시 설치하는 편이 안전합니다.

최초 1회 설정:

```bash
cd pangi
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
cp .env.example .env
```

로컬 실행:

먼저 `pangi/.env`의 빈 값을 로컬 환경에 맞게 채웁니다. `/health`만 확인할 때는 Slack 관련 값에 임시 문자열을 넣어도 됩니다.

```bash
cd pangi
source .venv/bin/activate
uvicorn pangi.app:app --reload --port 8000
```

상태 확인:

```bash
curl http://127.0.0.1:8000/health
```

관리자 DB 페이지:

`/pangi-admin/db`는 기본 비활성화 상태입니다. 서버에서 확인이 필요할 때만 `pangi/.env`에 아래 값을 설정합니다.

```env
PANGI_ENABLE_ADMIN_PAGES=1
PANGI_ADMIN_PASSWORD=change-this-password
```

서버를 재시작한 뒤 브라우저에서 `http://127.0.0.1:8000/pangi-admin/login`으로 접속합니다.

- 아이디: `pangi`
- 비밀번호: `PANGI_ADMIN_PASSWORD`에 설정한 값

테스트:

```bash
cd pangi
source .venv/bin/activate
pytest
```

## 다음 할 일

현재 다음 순서:

1. worktree 격리 붙이기
2. `codex exec --sandbox read-only` 실행 붙이기
3. Slack thread에 결과 반환
4. 1차 MVP end-to-end 검증

처음부터 크게 만들지 말고, Slack에서 팡이가 한 번 안정적으로 읽고 답하는 경험을 먼저 완성합니다.
