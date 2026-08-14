# WBS-01 Foundation과 Package 경계

## 요약

Pangi를 Greenfield Python Package로 시작하고 Domain, Application, Adapter, Plugin의 의존 방향을 고정한다. 이후 Work Package가 Framework 세부사항을 Core 계약에 역류시키지 않도록 저장소 구조와 공개 확장 지점을 먼저 만든다.

## 목표

- Python 3.11+에서 설치 가능한 최소 wheel과 개발 환경을 만든다.
- `domain → application → adapters`가 아니라 Adapter가 안쪽 계약을 구현하는 Dependency Rule을 강제한다.
- Public API와 내부 구현 Module을 구분한다.
- Built-in과 선택 설치 Capability Pack의 등록 경계를 정의한다.

## 선행 작업

- 없음

## 설계서 참조

- [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md): Section 4, 6.4~6.7, 18, 24 Phase 0, 25

## 범위

- `pyproject.toml`, `src/pangi`, `tests`, `ui`의 기본 구조
- Domain/Application/Adapter/Plugin Package와 Dependency 검사
- 안정 Public API의 빈 계약과 Python Entry Point Group
- Built-in Resource와 Capability Pack Manifest 위치
- 개발용 Lint, Type, Test 명령의 최소 기준

## 범위 밖

- 실제 Provider, MCP, Slack Adapter 구현
- SQLite Table과 Migration 내용
- Admin 화면 기능과 Capability Pack 업무 로직
- Legacy DB 또는 Legacy Runtime Code 이식

## 기술 설계

- Domain은 Pydantic/FastAPI/SQLite/Slack/MCP SDK를 Import하지 않는 순수 규칙 계층으로 둔다.
- Application은 Use Case, Request/Result 계약과 Port를 소유한다.
- Adapter는 Application Port를 구현하고 Composition Root만 구체 구현을 조합한다.
- `PangiConfig`, `PangiRuntime`, Public Request/Result/Event와 Plugin Protocol만 안정 API 후보로 노출한다.
- Provider, Channel, SecretStore, Subagent는 `pangi.*` Entry Point Group과 Versioned Manifest로 등록한다.
- Built-in Resource는 wheel 안에서 읽기 전용이고 사용자 데이터 경로와 물리적으로 분리한다.
- Architecture Test가 금지된 Import와 내부 Module의 Public re-export를 검사한다.

## 구현 체크리스트

- [x] `src` Layout과 Python 3.11+ Package Metadata를 만든다.
- [x] Domain, Application, Adapter, Plugin, Built-in Package Skeleton을 만든다.
- [x] `PangiRuntime` Facade와 Public Contract의 최소 Import Surface를 정의한다.
- [x] Provider, Channel, SecretStore, Subagent Entry Point Group을 선언한다.
- [x] Capability Pack Manifest의 Core 호환 Version 검증 지점을 만든다.
- [x] UI Source와 Package에 포함될 정적 Asset 경로를 분리한다.
- [x] Legacy Code와 Runtime Data를 Package에 포함하지 않도록 Ignore/Build 규칙을 둔다.
- [x] Architecture Dependency Test를 추가한다.

## 검증 체크리스트

- [x] 깨끗한 가상 환경에서 wheel Build와 Install Smoke Test를 실행한다.
- [x] `import pangi`가 선택 의존성 없이 성공하는지 확인한다.
- [x] Domain/Application에서 금지된 Framework Import가 실패하는 테스트를 실행한다.
- [x] 내부 Adapter Module이 안정 Public API로 노출되지 않는지 확인한다.
- [x] 설치하지 않은 Capability Pack이 Registry와 시작 시간에 영향을 주지 않는지 확인한다.

## 구현 결과

- PEP 621 Metadata와 `uv.lock`을 기준으로 작업 배포 이름 `pangi-agent`, Python `>=3.11`, 런타임 의존성 0개인 wheel을 구성했다.
- 안정 루트 API는 `PangiRuntime`, `__version__`으로 제한하고 구체 Adapter 조립은 `bootstrap.py`에 뒀다.
- `pangi.providers`, `pangi.channels`, `pangi.secret_stores`, `pangi.subagents`를 지연 발견하는 Registry와 Capability Pack 호환성 Policy 경계를 추가했다.
- Built-in JSON Resource, UI Source, wheel 정적 Asset, Runtime Data 경로를 서로 분리했다.
- Ruff, strict mypy, Pytest Architecture/Unit/Smoke Test와 깨끗한 Python 3.11 wheel 설치 검증을 통과했다.

## 완료 조건

- 최소 wheel을 설치하고 `pangi` Package와 CLI Entry Point를 불러올 수 있다.
- Core가 Adapter Framework를 Import하지 않는다.
- 안정 Public API와 내부 구현 경계가 Architecture Test로 고정된다.
- Legacy DB나 Legacy 실행 경로 없이 Greenfield로 시작한다.

## 미결정 사항

- 최종 PyPI 또는 Private Registry 배포 이름
- Public API의 1.0 안정화 시점
