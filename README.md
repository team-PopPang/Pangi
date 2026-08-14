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

현재 WBS 02의 설정, Runtime Data 초기화와 Read-only Doctor를 사용할 수 있다.

```bash
uv run pangi init --yes
uv run pangi config validate
uv run pangi doctor --offline
```

`pangi start`와 `pangi status`의 명령 계약은 준비됐지만 실제 Dashboard와 DB Runtime은 WBS 03·04에서 연결한다.
