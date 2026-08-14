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

현재 WBS 02의 설정·Runtime Data·CLI, WBS 03의 SQLite 기반과 WBS 04의 첫 Web Runtime을 사용할 수 있다.

```bash
uv run pangi init --yes
uv run pangi config validate
uv run pangi migrate plan
uv run pangi migrate apply --yes
uv run pangi doctor --offline
uv run pangi start
```

`pangi start`는 기본적으로 `http://127.0.0.1:8787`에서 빈 Admin Shell과 `/health/live`, `/health/ready`를 제공한다. 실행 상태는 다른 Terminal에서 `uv run pangi status --json`으로 확인한다. Bootstrap 인증과 관리 기능은 WBS 04의 후속 단계에서 연결한다.
