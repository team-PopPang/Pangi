# Orchestrator

## 역할

Orchestrator는 Slack 요청을 어떤 작업으로 처리할지 결정한다.

MVP에서는 복잡한 AI planner가 아니라 단순하고 예측 가능한 분류기다.

## 입력

- SlackCommand
- thread context
- repo allowlist
- 현재 job 상태

## 출력

- job type
- 실행 모드
- 승인 필요 여부
- prompt template
- 대상 repo key

## MVP job type

```text
analyze
edit_requested
pr_summary
troubleshooting
xcodebuild_failure
```

1차 MVP에서 실제 실행하는 것은 `analyze`다. 나머지는 감지하더라도 "아직 지원하지 않음" 또는 "분석 계획만 제공"으로 처리한다.

## 기본 판단 규칙

- "분석", "봐줘", "정리"는 `analyze`
- "수정", "고쳐", "리팩터링", "구현"은 `edit_requested`
- PR URL과 "요약"이 함께 있으면 `pr_summary`
- "빌드 실패", "xcodebuild"는 `xcodebuild_failure`
- "문서화", "트러블슈팅"은 `troubleshooting`

## MVP 실행 정책

- `analyze`: read-only Codex 실행
- `edit_requested`: read-only 분석만 실행하고 수정은 승인 단계로 미룬다.
- `pr_summary`: 1차 MVP에서는 미지원 또는 수동 안내
- `xcodebuild_failure`: 1차 MVP에서는 로그가 있으면 read-only 분석 후보
- `troubleshooting`: 1차 MVP에서는 Notion 없이 Slack 요약만 후보

## prompt 구성 원칙

- 결론 먼저
- 근거 파일 경로 표시
- 확인한 사실과 추정 분리
- 파일 수정 금지 여부 명시
- 검증 방법 포함
- 마지막 요약 포함

## 테스트 기준

- 분석 요청이 `analyze`로 분류된다.
- 수정 요청이 바로 `workspace-write`로 가지 않는다.
- PR 요청이 분석 job과 섞이지 않는다.
- 알 수 없는 요청도 안전하게 `analyze` 또는 안내 응답으로 처리된다.
