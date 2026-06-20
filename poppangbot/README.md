# PopPang Bot

Slack 커스텀 봇 "팝팡봇"의 최소 FastAPI 서버입니다. 지금 단계의 목표는 Slack Slash Command 요청을 받고 즉시 테스트 응답을 반환하는 것입니다.

## 로컬 실행

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

## Slack Request URL

로컬 서버는 Slack에서 직접 접근할 수 없으므로 ngrok 같은 터널링 도구로 외부 HTTPS 주소를 엽니다.

```bash
ngrok http 8000
```

Slack Slash Command의 Request URL에는 아래 형식으로 입력합니다.

```text
https://{ngrok주소}/slack/commands
```

Slack에서 `/팝팡`을 입력하면 서버가 요청 서명을 검증한 뒤 `팝팡봇 테스트 응답입니다`를 ephemeral 메시지로 반환합니다.

Slack Event Subscriptions의 Request URL에는 아래 형식으로 입력합니다.

```text
https://{서버도메인}/slack/events
```

## 서버 배포

저장소 루트에서 `deploy-bot.sh`를 실행하면 `poppangbot/` 전체를 서버로 전송하고 FastAPI 봇을 재시작합니다. 이 배포는 로컬 `poppangbot/.env`도 함께 전송합니다.

```bash
./deploy-bot.sh
```

기본 배포 설정:

```text
SSH_HOST=poppang-server
SERVER_DIR=/home/poppang/admin
BOT_DIR=poppangbot
BOT_PORT=4100
```

배포 후 서버 내부에서 확인:

```bash
curl http://127.0.0.1:4100/health
```

외부 HTTPS 프록시 연결 확인:

```bash
curl -i -X POST https://poppang.co.kr/slack/commands
curl -i -X POST https://poppang.co.kr/slack/events
```

위 명령은 Slack 서명 헤더 없이 직접 호출하므로 `401 Invalid Slack signature`가 나오면 봇 서버까지 요청이 도달한 것입니다.

Slack에서 사용하려면 외부 HTTPS 주소가 봇 서버로 연결되어야 합니다. 예를 들어 nginx 또는 Caddy에서 `/slack/`를 `http://127.0.0.1:4100/slack/`로 프록시한 뒤 Slack Request URL에 아래 형식으로 입력합니다.

```text
https://{서버도메인}/slack/commands
```

Event Subscriptions Request URL:

```text
https://{서버도메인}/slack/events
```

## 환경변수

실제 값은 `poppangbot/.env`에 저장하고 커밋하지 않습니다. 예시는 `.env.example`을 참고합니다.

```bash
cp .env.example .env
```

필수 값:

- `SLACK_SIGNING_SECRET`: Slack 요청 서명 검증에 사용합니다.

보조 값:

- `SLACK_CLIENT_ID`
- `SLACK_CLIENT_SECRET`
- `SLACK_VERIFICATION_TOKEN`: Slack 구형 검증 토큰입니다. 새 요청 검증은 signing secret을 사용합니다.
- `SLACK_BOT_TOKEN`: `xoxb-`로 시작하는 Bot User OAuth Token입니다. `@팝팡` 멘션에 답장할 때 사용합니다.
- `SLACK_ALLOWED_COMMANDS`: 쉼표로 구분한 허용 slash command 목록입니다.

## 멘션 이벤트 설정

`@팝팡 메시지` 방식으로 봇을 호출하려면 Slack API 앱 설정에서 Events API를 사용합니다.

1. `OAuth & Permissions`에서 Bot Token Scopes에 `app_mentions:read`, `chat:write`를 추가합니다.
2. `OAuth & Permissions`의 `Bot User OAuth Token` 값을 `SLACK_BOT_TOKEN`에 저장합니다.
3. `Install App`에서 앱을 워크스페이스에 다시 설치합니다.
4. `Event Subscriptions`를 켭니다.
5. Request URL에 `https://{서버도메인}/slack/events`를 입력합니다.
6. `Subscribe to bot events`에 `app_mention`을 추가합니다.
7. Slack 채널에서 `/invite @팝팡`으로 봇을 초대합니다.

Slack에서 `@팝팡 테스트`처럼 멘션하면 서버가 이벤트를 받고 같은 스레드에 테스트 답장을 보냅니다.

## 이후 Codex 연동 방향

Codex 연동은 최소 테스트 이후 별도 단계로 진행합니다. Slack은 3초 안에 응답을 받아야 하므로, 긴 코드 작업은 즉시 접수 응답을 보낸 뒤 백그라운드 작업으로 처리해야 합니다.

```text
Slack slash command
-> FastAPI /slack/commands
-> 즉시 접수 응답
-> 백그라운드 작업
-> Codex CLI codex exec 또는 Codex SDK 실행
-> Slack response_url 또는 Web API로 결과 응답
```
