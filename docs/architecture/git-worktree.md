# Git Worktree Manager

## 역할

Git Worktree Manager는 Codex가 원본 repo를 직접 건드리지 않도록 Slack thread마다 하나의 thread workspace를 만들고, 그 안에 repo별 checkout을 준비한다.

이 문서의 핵심 기준은 아래와 같다.

- Slack thread 1개 = active thread workspace 1개
- Slack thread 1개 = active Codex session 1개
- 같은 thread 안에서는 repo가 여러 개여도 같은 session을 유지한다.

## 기본 경로

```text
source repo:       /home/poppang/admin/pangi/repos/PopPang-iOS
worktree root:     /home/poppang/admin/pangi/.data/worktrees
thread workspace:  /home/poppang/admin/pangi/.data/worktrees/_threads/{slack_thread_id}
repo workspace:    /home/poppang/admin/pangi/.data/worktrees/_threads/{slack_thread_id}/repos/{repo_key}
```

실제 source repo는 `PANGI_SOURCE_REPO_ROOT` 하위 direct child repo에서 가져온다.
Slack 메시지에서 임의 경로를 받지 않는다.

## read-only 분석 규칙

repo checkout은 여전히 detached worktree를 사용한다.

```text
git worktree add --detach {repo_workspace_path} origin/{base_branch}
```

`base_branch`는 `PANGI_DEFAULT_BASE_BRANCH`로 정한다.
기본값은 `develop`이다.
read-only repo checkout 생성 시 먼저 `origin/develop`을 시도하고, branch가 없으면 `origin/main`을 한 번 더 시도한다.

```env
PANGI_DEFAULT_BASE_BRANCH=develop
```

수정 승인 흐름이 붙는 뒤 단계에서는 별도 작업 branch 규칙을 다시 사용한다.

## MVP 흐름

```text
source repo root child lookup
-> git repo 확인
-> thread workspace 경로 계산
-> repo workspace 경로 계산
-> git fetch origin
-> repo workspace가 없으면 git worktree add --detach
-> repo workspace가 있으면 재사용
-> Codex Runner에는 thread workspace root를 전달
```

Codex는 repo workspace 자체가 아니라 thread workspace root에서 실행한다.
repo 분석 prompt 안에 `repo_path`를 넣어 어떤 checkout을 읽어야 하는지 알려준다.

## cleanup

active session이 1시간 이상 idle이면 아래 순서로 정리한다.

```text
codex archive
-> slack_threads.active_codex_session_id 해제
-> thread workspace cleanup
```

cleanup이 실패해도 다음 turn에서 새 session을 만들 수 있도록 active 연결은 먼저 끊는다.

## diff 수집

분석 모드에서는 원칙적으로 diff가 없어야 한다.
PR 승인 전 diff 수집은 수정 승인 흐름 단계에서 본격적으로 붙인다.

```bash
git -C {repo_workspace_path} status --short
git -C {repo_workspace_path} diff --stat
git -C {repo_workspace_path} diff --name-only
```

## 안전 규칙

- Codex cwd가 source repo면 실행하지 않는다.
- Codex cwd가 thread workspace root 하위가 아니면 실행하지 않는다.
- source repo를 직접 수정하지 않는다.
- main/develop 같은 기본 브랜치에서 직접 작업하지 않는다.
- repo workspace path가 이미 디렉터리면 재사용한다.
- repo workspace path가 파일이면 실패 처리한다.

## 테스트 기준

- 임시 git repo에서 repo workspace 생성 성공
- 같은 thread/repo 요청 시 기존 repo workspace 재사용
- 잘못된 repo path 실패
- repo workspace path가 파일일 때 실패
- source repo 직접 실행 방지
