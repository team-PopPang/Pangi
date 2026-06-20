# Approval Flow

## 역할

Approval Flow는 코드 수정과 PR 생성을 사용자의 Slack 승인 뒤에만 실행하게 만든다.

1차 MVP에서는 구현하지 않지만, 설계 원칙은 미리 고정한다.

## 승인 단계

```text
read-only 분석
-> 수정 계획 Slack 응답
-> 수정 실행 승인
-> workspace-write 실행
-> diff Slack 응답
-> PR 생성 승인
-> 서버가 commit/push/PR 생성
```

## 승인 대상

- workspace-write 실행
- PR 생성
- 실패 후 재시도
- 작업 취소

## 승인자

MVP 기준:

- 원 요청자
- admin allowlist 사용자

아무 Slack 사용자나 승인 버튼을 눌러 작업을 진행할 수 없어야 한다.

## Codex 권한 원칙

- Codex는 코드 분석과 수정만 담당한다.
- Codex가 commit/push/PR 생성을 직접 하지 않는다.
- 서버가 diff 확인, commit, push, PR 생성을 통제한다.

## 테스트 기준

- 승인 없는 workspace-write 실행 차단
- 승인자 allowlist 검사
- 승인 후 job enqueue
- 거절 후 job rejected 처리
- PR 생성 승인과 수정 승인 구분
