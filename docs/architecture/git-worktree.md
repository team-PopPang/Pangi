# Git Worktree Manager

## 역할

Worktree Manager는 Codex가 원본 repo를 직접 건드리지 않도록 job마다 격리된 작업 공간을 만든다.

1차 MVP의 우선순위는 코드 수정이 아니라 코드 읽기다. 따라서 현재 read-only 분석 흐름에서는 새 작업 branch를 만들지 않고 `origin/{base_branch}`를 detached checkout으로 가져와 Codex가 읽을 격리 폴더로 사용한다.

## 기본 경로

```text
source repo:   /home/poppang/admin/pangi/repos/PopPang-iOS
worktree root: /home/poppang/admin/pangi/.data/worktrees
job worktree:  /home/poppang/admin/pangi/.data/worktrees/{job_id}
```

실제 경로는 `PANGI_SOURCE_REPO_ROOT` 하위 direct child repo에서 가져온다. Slack 메시지에서 임의 경로를 받지 않는다.

## read-only 분석 규칙

```text
git worktree add --detach {worktree_path} origin/{base_branch}
```

`base_branch`는 `PANGI_DEFAULT_BASE_BRANCH`로 정한다. 기본값은 `develop`이다. read-only worktree 생성 시 먼저 `origin/develop`을 시도하고, branch가 없으면 `origin/main`을 한 번 더 시도한다.

```env
PANGI_DEFAULT_BASE_BRANCH=develop
```

수정 승인 흐름이 붙는 뒤 단계에서는 별도 작업 branch 규칙을 다시 사용한다.

```text
pangi/job-{job_id_short}
```

## MVP 흐름

```text
source repo root child lookup
-> git repo 확인
-> git fetch origin
-> git worktree add --detach {worktree_path} origin/{base_branch}
-> worktree path 저장
-> Codex Runner에 전달
```

## diff 수집

분석 모드에서는 원칙적으로 diff가 없어야 한다. PR 승인 전 diff 수집은 수정 승인 흐름 단계에서 본격적으로 붙인다.

```bash
git status --short
git diff --stat
git diff --name-only
```

## 안전 규칙

- Codex cwd가 source repo면 실행하지 않는다.
- Codex cwd가 worktree root 하위가 아니면 실행하지 않는다.
- main/develop 같은 기본 브랜치에서 직접 작업하지 않는다.
- 이미 존재하는 worktree path를 덮어쓰지 않는다.
- cleanup은 명시적 정책이 생기기 전까지 신중하게 처리한다.

## 테스트 기준

- 임시 git repo에서 worktree 생성 성공
- 잘못된 repo path 실패
- 이미 존재하는 worktree path 실패
- status/diff 수집 성공
- source repo 직접 실행 방지
