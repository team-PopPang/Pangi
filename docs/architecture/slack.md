# Slack 계층

## 역할

Slack 계층은 팀원이 팡이를 부르는 입구다. Slack 요청을 검증하고 내부 command 객체로 바꾼 뒤, 긴 작업은 background job으로 넘긴다.

## 담당 범위

- `/slack/events`
- `/slack/commands`
- `/slack/interactions`
- Slack request signature 검증
- `url_verification` 처리
- `app_mention` 처리
- Slack thread id 계산
- Slack retry 중복 방지
- Slack Web API 응답 전송
- Slack Web API reaction 전송

## MVP 처리 흐름

```text
Slack app_mention
-> FastAPI /slack/events
-> signature 검증
-> allowlist 확인
-> 원본 메시지에 eyes reaction 추가
-> SlackCommand 생성
-> Orchestrator에 전달
-> 즉시 200 OK
-> background job에서 thread 응답
```

## 내부 command 필드

```text
team_id
channel_id
user_id
text
thread_ts
event_id
```

`thread_ts`는 아래 규칙으로 정한다.

- Slack event에 `thread_ts`가 있으면 사용한다.
- 없으면 원본 event의 `ts`를 사용한다.

## 필수 안전 규칙

- Slack signature 검증 전 payload를 신뢰하지 않는다.
- timestamp tolerance는 5분을 기본으로 한다.
- `bot_id` 또는 bot subtype 이벤트는 무시한다.
- Slack retry header가 있으면 중복 job 생성을 막는다.
- 허용되지 않은 user/channel 요청은 job으로 만들지 않는다.

## 테스트 기준

- valid signature 요청 성공
- invalid signature 요청 401
- stale timestamp 요청 401
- `url_verification` challenge 반환
- `app_mention` 정규화 성공
- bot message 무시
- retry event 중복 방지
