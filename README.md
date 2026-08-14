# Pangi

조직이 설치해 운영하는 경량 Agent Runtime

## 문서

- [Pangi 1.0 재설계 구현 설계서](docs/pangi-rebuild-implementation-design.md)
- [Pangi 1.0 구현 WBS](docs/chunks/README.md)

## 개발 환경

Python 3.11 이상과 `uv`가 필요하다. 잠금 파일에 맞춰 개발 의존성을 설치한 뒤 같은 검증 명령을 로컬과 CI에서 사용한다.

```bash
uv sync --extra dev --python 3.11
uv run ruff check .
uv run mypy src
uv run pytest
```

## 첫 실행 기반

현재 WBS 02의 설정·Runtime Data·CLI, WBS 03의 SQLite 기반과 WBS 04의 Web Runtime·최초 관리자 생성·Local Login 경로를 사용할 수 있다.

```bash
uv run pangi init --yes
# 출력된 http://127.0.0.1:8787/bootstrap#... URL을 보관한다.
uv run pangi config validate
uv run pangi migrate plan
uv run pangi doctor --offline
uv run pangi start
```

`pangi init`은 인증 Migration을 적용하고 기본 30분짜리 일회성 Bootstrap URL을 최초 한 번만 출력한다. URL을 잃었거나 만료됐다면 Admin 생성 전에만 `uv run pangi bootstrap rotate --yes`로 기존 Grant를 취소하고 새 URL을 발급할 수 있다. `pangi start` 후 URL을 열어 Local Admin을 만들고 `/login`에서 같은 계정으로 로그인한다.

Runtime은 기본적으로 `http://127.0.0.1:8787`에서 Admin Shell과 `/health/live`, `/health/ready`를 제공한다. 실행 상태는 다른 Terminal에서 `uv run pangi status --json`으로 확인한다. Session은 기본 12시간 동안 유지되고 30분 이후 UI에서 명시적으로 회전할 수 있다. Loopback이 아닌 Host에서는 HTTPS가 아니면 로그인을 거부한다.
