# Orchestrator

## 역할

입력 가드레일은 Slack 요청 중 서버가 처리하면 안 되는 요청을 먼저 차단하고, 코드로 확실히 판단 가능한 요청을 1차 라우팅한다.
Orchestrator는 입력 가드레일이 애매하다고 남긴 요청만 보조 판정한다.

팡이는 기본적으로 AI 대화 봇이다. 다만 PopPang repo를 명확히 분석해달라는 요청만 repo analysis job으로 승격한다.

## 처리 순서

```text
SlackCommand
-> 입력 가드레일
-> 확실한 요청은 일반 대화 / 안내 응답 / repo analysis job
-> 애매한 요청만 Orchestrator
-> 정책 보정
-> 일반 대화 / 안내 응답 / repo analysis job
```

입력 가드레일은 코드로 실행되는 deterministic 단계다. 외부 웹/URL 분석, 코드 수정, PR 생성, 배포, commit/push 같은 MVP 범위 밖 요청은 Orchestrator로 보내기 전에 차단한다. 일반 대화, repo 불명확, 허용 repo 분석처럼 코드로 확실히 판단 가능한 요청도 Orchestrator로 보내지 않는다.

Orchestrator는 입력 가드레일이 `ambiguous`로 남긴 요청만 받아 `codex_chat`, `needs_repo`, `repo_analysis` 중 어느 흐름으로 보낼지 결정한다. Codex CLI 기반 orchestrator가 비정상 decision을 반환해도 정책 보정 단계에서 repo allowlist, 원문 repo 명시 여부, `should_create_job` 조건을 다시 강제한다.

입력 가드레일 상세 기준은 [input-guardrail.md](input-guardrail.md)를 따른다.

## 모델 정책

운영에서는 Codex CLI 기반 orchestrator adapter를 사용한다.

```text
model: gpt-5.4-mini
reasoning effort: low
timeout: 20 seconds
output: JSON schema
```

`PANGI_ORCHESTRATOR_MODEL`은 Codex CLI의 `--model` 옵션으로 전달한다. `PANGI_ORCHESTRATOR_REASONING_EFFORT`는 `-c model_reasoning_effort="..."`로 전달한다. Orchestrator는 repo를 깊게 읽는 단계가 아니라 입력 가드레일이 확신하지 못한 요청의 방향성을 보조 판정하는 단계이므로 기본값은 mini 모델과 `low` reasoning을 사용한다.

일반 대화는 `PANGI_CHAT_MODEL` 기본값 `gpt-5.4-mini`, reasoning `low`를 사용하고, 실제 repo read-only 분석은 `PANGI_ANALYSIS_MODEL` 기본값 `gpt-5.5`, reasoning `high`를 사용한다.

테스트와 일부 로컬 검증에서는 deterministic orchestrator를 직접 주입할 수 있지만, 기본 런타임은 HTTP AI API를 직접 호출하지 않고 Codex CLI를 호출한다.

Codex orchestrator의 런타임 지시는 코드에 직접 쓰지 않고 `pangi/src/pangi/prompts/orchestrator.md`에서 읽는다. 이 파일은 요청 분류 규칙만 담고, 일반 대화나 repo 분석 답변 규칙은 각 실행 모드의 prompt에서 관리한다.

## 입력

- SlackCommand
- repo allowlist
- 현재 thread context

## 출력

```text
classification
should_create_job
repo_key
reply_text
reason
```

## MVP 요청 분류

```text
codex_chat
blocked_web_analysis
needs_repo
repo_analysis
unsupported
```

### `codex_chat`

일반 대화, 인사, 텍스트 정리, repo를 직접 읽지 않는 분석 요청이다.

```text
@팡이 안녕
@팡이 이거 어떻게 생각해?
@팡이 이 문장 정리해줘
@팡이 분석해줘
```

repo job을 만들지 않고 Codex chat 응답만 Slack thread에 남긴다.

### `blocked_web_analysis`

외부 웹, 인터넷 검색, 뉴스, 기사, 임의 URL 분석 요청이다.

```text
@팡이 https://example.com 분석해줘
@팡이 인터넷에서 찾아줘
@팡이 뉴스 요약해줘
```

서버 부하와 보안 이유로 job을 만들지 않고 안내 응답만 보낸다.

### `needs_repo`

repo나 코드 분석 의도는 있지만 대상 repo가 명확하지 않은 요청이다.

```text
@팡이 레포 분석해줘
@팡이 코드 구조 봐줘
```

job을 만들지 않고 어느 repo를 볼지 질문한다.

### `repo_analysis`

허용된 repo key가 있고, repo를 읽어야 하는 분석 요청이다.

```text
@팡이 PopPang-iOS 구조 분석해줘
@팡이 Admin 로그인 흐름 봐줘
```

이 경우에만 AgentJob을 만들고 job별 read-only worktree에서 Codex를 실행한다.

### `unsupported`

코드 수정, PR 생성, 배포, commit/push처럼 MVP 범위 밖의 요청이다.

```text
@팡이 PopPang-iOS 수정해줘
@팡이 PR 만들어줘
@팡이 배포해줘
```

MVP에서는 read-only 분석만 가능하다고 안내한다.

## hard guardrail

아래 정책은 AI 판단에 맡기지 않고 코드로 강제한다.

- Slack user/channel allowlist
- repo allowlist
- 외부 URL 분석은 Orchestrator 호출 전에 차단
- 수정/PR/배포 요청은 Orchestrator 호출 전에 차단
- `repo_analysis`인데 repo key가 allowlist 밖이면 `needs_repo`로 downgrade
- `repo_analysis`인데 repo key가 원문에 명시되지 않았으면 `needs_repo`로 downgrade
- `should_create_job`은 `repo_analysis`에서만 true 허용

## 테스트 기준

- 입력 가드레일이 확실히 분류한 일반 대화는 Orchestrator를 호출하지 않는다.
- 입력 가드레일이 확실히 분류한 URL 요청은 Orchestrator를 호출하지 않는다.
- 입력 가드레일이 확실히 분류한 repo 없는 코드 분석 요청은 Orchestrator를 호출하지 않는다.
- 입력 가드레일이 확실히 분류한 허용 repo 분석 요청은 Orchestrator를 호출하지 않는다.
- 애매한 요청만 Orchestrator를 호출한다.
