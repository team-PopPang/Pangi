# 팡이 MVP 개요

## 목표

팡이 1차 MVP는 Slack에서 팡이와 기본 AI 대화를 할 수 있고, 허용된 PopPang repo 분석 요청은 서버가 격리된 git worktree에서 `codex exec --sandbox read-only`를 실행한 뒤 결과를 Slack thread에 반환하는 것이다.

처음부터 코드 수정, PR 생성, Notion 기록, session resume까지 만들지 않는다. 먼저 "팡이가 대화하고, 필요한 repo만 안전하게 읽고 답한다"는 경험을 완성한다.

## 제품 컨셉

```text
팡이가 먼저 보고, 팀이 더 빠르게 결정합니다.
```

팡이는 PopPang 팀 옆에서 같이 고민하는 AI 동료다. Slack에서 편하게 부를 수 있고, 일반 대화는 바로 답하며, PopPang repo 분석은 코드를 먼저 확인한 뒤 판단에 필요한 근거를 짧게 정리해준다.

## 현재 저장소 역할

```text
poppangbot/  Slack 연결 검증용 FastAPI 샘플
pangi/       앞으로 만들 실제 팡이 Python 패키지
docs/        설계와 구현 체크리스트
```

`poppangbot/`은 완성 플랫폼이 아니다. 기존 Slack 서명 검증, slash command, app mention 처리 흐름을 참고하는 샘플이다.

## MVP 아키텍처

```text
Slack
-> FastAPI Webhook
-> 입력 가드레일
-> Orchestrator
-> 일반 대화는 Codex Chat
-> repo 분석은 Job Worker
-> Worktree Manager
-> Codex Runner
-> 출력 가드레일
-> Markdown to Slack
-> Slack Thread Reply
```

## 1차 MVP 포함 범위

- Slack app mention 수신
- Slack request signature 검증
- user/channel/repo allowlist
- 입력 가드레일 기반 외부 웹/쓰기 요청 차단과 코드 기반 1차 라우팅
- 애매한 요청만 Codex CLI orchestrator를 통한 보조 분류
- repo worktree 없는 Codex chat 응답
- 일반 대화와 orchestrator는 mini 모델, 실제 repo read-only 분석은 강한 분석 모델로 분리
- Slack thread 단위 job 생성
- background job 실행
- job별 git worktree 생성
- `codex exec --sandbox read-only` 실행
- stdout/stderr/exit code/timeout 수집
- 출력 가드레일 기반 secret redaction과 길이 제한
- Slack bot 응답 전용 Markdown to Slack 변환
- Slack thread에 결과 반환
- 실패/timeout 메시지 반환

## 1차 MVP 제외 범위

- 외부 웹/인터넷 URL 분석
- 코드 수정
- PR 생성
- Notion report
- Codex session resume
- xcodebuild 자동 실행
- dashboard
- LLM router
- prompt 관리 화면
- eval 플랫폼

## 다음 단계

1차 MVP가 안정화되면 아래 순서로 붙인다.

1. Notion episode report
2. Slack 승인 버튼
3. `workspace-write` 수정 실행
4. diff 반환
5. PR 생성 승인
6. 서버 주도 commit/push/PR 생성

## 구현 기준

구현 순서는 [../implementation-checklist.md](../implementation-checklist.md)를 따른다. 작업별 세부 설계는 `docs/architecture/` 아래 문서를 먼저 읽는다.
