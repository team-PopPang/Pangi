# Slack Event 설정 가이드

이 문서는 Slack에서 `@팝팡`처럼 봇을 멘션했을 때 `poppangbot` FastAPI 서버가 이벤트를 받고 답장하도록 설정하는 절차를 정리합니다.

## 현재 구조

Slash Command와 멘션 이벤트는 동작 방식이 다릅니다.

```text
/팝팡
-> Slack Slash Command
-> POST https://poppang.co.kr/slack/commands
-> FastAPI가 바로 응답
```

```text
@팝팡 테스트
-> Slack Events API
-> POST https://poppang.co.kr/slack/events
-> FastAPI가 이벤트를 받고 200 OK 반환
-> FastAPI가 Slack Web API로 답장 전송
```

멘션 이벤트 답장에는 Slack Bot Token이 필요합니다.

## 필요한 Slack 권한

Slack API의 앱 설정에서 `OAuth & Permissions`로 이동합니다.

`Bot Token Scopes`에 아래 권한이 있어야 합니다.

```text
app_mentions:read
chat:write
```

- `app_mentions:read`: 채널에서 `@팝팡`으로 멘션된 이벤트를 받기 위한 권한입니다.
- `chat:write`: 봇이 Slack 채널이나 스레드에 메시지를 쓰기 위한 권한입니다.

권한을 추가하거나 변경한 뒤에는 반드시 앱을 다시 설치합니다.

```text
Install App -> Reinstall to Workspace
```

## SLACK_BOT_TOKEN

`SLACK_BOT_TOKEN`은 봇이 Slack Web API를 호출할 때 사용하는 토큰입니다.

Slack API 앱 설정에서 아래 위치에서 확인합니다.

```text
OAuth & Permissions
-> Bot User OAuth Token
```

토큰은 보통 아래 형태입니다.

```text
xoxb-...
```

서버와 로컬의 `poppangbot/.env`에 저장합니다.

```env
SLACK_BOT_TOKEN=xoxb-...
```

실제 토큰값은 Git에 커밋하지 않습니다.

## Event Subscriptions 설정

Slack API 앱 설정에서 `Event Subscriptions`로 이동합니다.

1. `Enable Events`를 `On`으로 켭니다.
2. `Request URL`에 아래 주소를 입력합니다.

```text
https://poppang.co.kr/slack/events
```

3. Slack이 URL 검증 요청을 보내고, 정상이라면 `Verified`가 표시됩니다.
4. `Subscribe to bot events`를 펼칩니다.
5. `Add Bot User Event`를 클릭합니다.
6. 아래 이벤트를 추가합니다.

```text
app_mention
```

7. 화면 아래쪽의 `Save Changes`를 누릅니다.
8. `Install App`에서 앱을 다시 설치합니다.

```text
Reinstall to Workspace
```

주의: `Request URL`이 `Verified`여도 `Subscribe to bot events`에 `app_mention`이 없으면 `@팝팡` 멘션 이벤트가 서버로 오지 않습니다.

## Slack 채널 설정

멘션을 받을 채널에 봇을 초대합니다.

```text
/invite @팝팡
```

그다음 채널에서 테스트합니다.

```text
@팝팡 테스트
```

정상 동작하면 봇이 테스트 응답을 보냅니다.

## 서버 배포

로컬에서 `.env`를 수정했거나 `poppangbot` 코드를 수정했다면 다시 배포합니다.

```bash
./deploy-bot.sh
```

배포 후 서버 내부에서 확인합니다.

```bash
curl http://127.0.0.1:4100/health
```

외부 HTTPS 프록시 연결은 아래 명령으로 확인합니다.

```bash
curl -i -X POST https://poppang.co.kr/slack/events
```

Slack 서명 없이 직접 호출하므로 아래 응답이 나오면 정상입니다.

```text
401 Invalid Slack signature
```

이 응답은 `https://poppang.co.kr/slack/events`가 nginx를 거쳐 `poppangbot` 서버까지 도달했다는 뜻입니다.

## 서버 로그 확인

서버에서 봇 로그를 확인합니다.

```bash
tail -f /home/poppang/admin/poppangbot/nohup.out
```

Slack에서 `@팝팡 테스트`를 보냈을 때 로그에 아래처럼 찍히면 이벤트가 서버에 도달한 것입니다.

```text
POST /slack/events
```

## 자주 생기는 문제

### Request URL은 Verified인데 아무 반응이 없음

대부분 `Subscribe to bot events`에 `app_mention`이 추가되지 않은 상태입니다.

화면에 아래처럼 보이면 이벤트가 추가되지 않은 것입니다.

```text
No events added yet.
```

`Add Bot User Event`에서 `app_mention`을 추가하고 `Save Changes`를 누릅니다.

### 권한을 추가했는데 반응이 없음

Slack 앱 권한을 바꾸면 워크스페이스에 다시 설치해야 합니다.

```text
Install App -> Reinstall to Workspace
```

### 채널에서 멘션해도 반응이 없음

봇이 채널에 초대되어 있는지 확인합니다.

```text
/invite @팝팡
```

또한 멘션을 입력할 때 Slack 자동완성에서 실제 앱/봇을 선택해야 합니다. 단순 텍스트로 `@팝팡`을 입력하면 이벤트가 오지 않을 수 있습니다.

### 서버 로그에 아무것도 안 찍힘

Slack에서 서버로 이벤트를 보내지 않는 상태입니다.

확인할 항목:

- Event Subscriptions가 `On`인지
- Request URL이 `Verified`인지
- `Subscribe to bot events`에 `app_mention`이 있는지
- `Save Changes`를 눌렀는지
- 앱을 `Reinstall to Workspace` 했는지
- 봇을 채널에 초대했는지

### 서버 로그에 502가 찍힘

서버가 이벤트는 받았지만 Slack Web API 답장 전송에 실패한 상태입니다.

확인할 항목:

- `SLACK_BOT_TOKEN`이 서버 `.env`에 있는지
- 토큰이 `xoxb-`로 시작하는 Bot User OAuth Token인지
- `chat:write` 권한이 있는지
- 권한 추가 후 앱을 다시 설치했는지
- 봇이 해당 채널에 있는지

### 스레드 댓글이 아니라 일반 채팅으로 답하고 싶음

현재 구현은 원문 메시지의 스레드에 답하도록 `thread_ts`를 사용합니다.

```text
channel + text + thread_ts
-> 스레드 댓글
```

일반 채팅으로 답하려면 `chat.postMessage` 호출에서 `thread_ts`를 빼면 됩니다.

```text
channel + text
-> 채널 일반 메시지
```

다만 Codex 연동처럼 응답이 길거나 작업 진행 상황을 남기는 기능은 스레드 답장이 더 관리하기 좋습니다.
