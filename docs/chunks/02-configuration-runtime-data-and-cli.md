# WBS-02 설정·Runtime Data·CLI

## 요약

Package 설치 후 사용자가 `pangi init`, `pangi doctor`, `pangi start`만으로 안전한 로컬 인스턴스를 시작할 수 있는 설정과 CLI Lifecycle을 만든다.

## 목표

- Config, Data, Secret, Backup과 Package Environment를 분리한다.
- 대화형/비대화형 초기화가 기존 파일을 덮어쓰지 않게 한다.
- CLI의 안정된 명령 구조와 JSON 출력 계약을 정의한다.
- 시작 전에 문제를 찾는 Read-only `doctor`를 제공한다.

## 선행 작업

- WBS-01

## 설계서 참조

- [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md): Section 18.4, 19, 20, 24 Phase 0, 27

## 범위

- Typed TOML Config와 경로 해석
- OS Application Data 기본 경로와 선택형 Project-local Mode
- `init`, `start`, `status`, `doctor`, `config`, `version` 명령
- `.gitignore` Marker Block의 멱등 추가
- Bootstrap Admin URL과 첫 실행 안내
- Text/JSON 출력의 Secret Redaction

## 범위 밖

- 실제 DB Schema와 Migration 구현
- Service 설치, Upgrade/Rollback 실행
- 외부 Provider/MCP/Slack의 완전한 연결 로직
- Dashboard 기능 화면

## 기술 설계

- 설정 우선순위와 경로는 하나의 `PangiConfig` Loader에서 결정하고 알 수 없는 Key를 오류로 보고한다.
- Runtime Data는 OS 기본 경로를 사용하고 사용자가 Project-local Mode를 선택할 때만 `.pangi/`를 만든다.
- 명시적 경로, `PANGI_HOME`, OS 기본 경로 순으로 해석하고 Project-local Mode를 명시하면 다른 경로보다 우선한다.
- Config 이름은 `pangi.toml`로 통일하고 Linux는 XDG, macOS는 Application Support/Logs 경로를 사용한다.
- `init`는 Plan/Preview 뒤 생성하며 기존 Config, DB, Secret을 덮어쓰지 않는다.
- `.gitignore`는 시작/종료 Marker Block을 기준으로 중복 없이 갱신한다.
- `doctor`는 Runtime→Path→Config→SQLite→Secret→Process→외부 연결→Product Integrity 순으로 검사한다.
- `doctor --offline --json`은 외부 호출 없이 Stable `schema_version`과 검사 ID를 반환한다.
- CLI 출력 Layer는 Secret, Token, 원문 Prompt/Tool Result를 Text와 JSON 모두에서 제거한다.

## 구현 체크리스트

- [x] `PangiConfig` Schema와 환경별 기본값을 정의한다.
- [x] Config/Data/Log/Backup/Vault 경로 Resolver를 구현한다.
- [x] `pangi init`의 Preview, 확인, 멱등 생성과 Non-interactive Mode를 구현한다.
- [x] Project-local `.gitignore` Marker Block 갱신을 구현한다.
- [ ] `pangi start`, `status`, `version`, `config path/validate`를 연결한다.
- [x] `doctor` 검사 Registry, 상태와 종료 코드를 구현한다.
- [x] `doctor --offline`, `--json`, `--strict` 계약을 구현한다.
- [x] Bootstrap URL을 일회성으로 생성하고 첫 Admin 생성 뒤 폐기하는 Port를 정의한다.

## 검증 체크리스트

- [x] 깨끗한 임시 Home에서 `init`를 두 번 실행해 동일 결과를 확인한다.
- [x] 기존 Config와 사용자 파일을 덮어쓰지 않는지 확인한다.
- [x] Project-local Mode에서 `.pangi/`가 정확히 한 번 Ignore되는지 확인한다.
- [x] `doctor --offline --json` Schema와 종료 코드 0/1/2를 Contract Test로 고정한다.
- [x] Text/JSON 출력에 Secret Pattern이 나타나지 않는지 검사한다.
- [ ] 잘못된 경로 권한, Port 충돌과 Config 오류가 안전한 다음 행동을 제공하는지 확인한다.

## 1차 구현 결과

- Pydantic 기반 `PangiConfig`와 Typer 명령 구조를 추가하되 Domain/Application 계층에는 두 Framework를 Import하지 않았다.
- OS/`PANGI_HOME`/Project-local 경로 Resolver와 기존 파일을 보존하는 `init` Plan/Apply 흐름을 구현했다.
- Read-only Doctor가 로컬 Runtime, Path, Config, Process, Package 무결성을 검사하고 미구현 Adapter는 `SKIP`으로 반환한다.
- `start`, `status` 명령과 Runtime/Bootstrap Port는 정의했지만 실제 Dashboard·DB Adapter가 없으면 명시적으로 unavailable을 반환한다.
- 실제 Dashboard 실행과 Port 충돌·권한 실패의 전체 검증이 남아 있으므로 WBS 상태는 `진행 중`으로 유지한다.

## 완료 조건

- 새 Host에서 `init`와 `start`만으로 Local Dashboard 시작 경로를 제공한다.
- 정상 설치에서 `doctor`가 `FAIL` 없이 종료 코드 0을 반환한다.
- `doctor --offline --json`을 설치 CI에서 실행할 수 있다.
- Runtime Data와 Secret이 Source Tree 밖에 있고 Project-local Data는 Gitignore된다.

## 미결정 사항

- Bootstrap Admin URL의 기본 만료 시간
