# WBS-03 SQLite 영속성과 Migration

## 요약

단일 조직·단일 Host 운영에 필요한 SQLite 실행 기반을 만들고, 각 기능 WBS가 자기 Schema와 Repository를 안전하게 추가할 수 있는 Transaction, Migration과 DB Snapshot 계약을 제공한다.

## 목표

- 외부 DB 없이 기능 상태를 영속화할 공통 SQLite 기반을 제공한다.
- 단일 Process/단일 Writer Profile을 코드와 진단으로 강제한다.
- Application 계층의 Unit of Work 계약과 SQLite Transaction 상태 전이를 고정한다.
- Migration Checksum과 호환성 규칙을 고정한다.
- Secret과 대용량 외부 원문을 DB에 저장하지 않는다.

## 선행 작업

- WBS-01
- WBS-02

## 설계서 참조

- [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md): Section 14, 18.1~18.3, 19.6~19.7, 20, 24 Phase 0

## 범위

- SQLite Connection/Write Coordinator와 Unit of Work
- `schema_migrations`와 Package Migration 실행 기반
- 기능 WBS별 Schema/Migration/Repository 소유권 규칙
- SQL Migration Package Resource와 Checksum
- SQLite Backup API 기반 DB Snapshot, Manifest와 읽기 전용 검증
- 기능 데이터와 운영 Backup의 Retention 소유권 규칙
- Health/Doctor의 DB 검사

## 범위 밖

- PostgreSQL/External Queue Adapter
- Vector DB와 Embedding Search
- Legacy SQLite Schema의 자동 Migration
- OAuth Token과 Secret 본문 저장
- Section 14.3 기능 Table의 선행 일괄 생성
- 기능별 Domain Model과 Repository Adapter 구현
- Config·Skill·Eval·Asset을 포함한 전체 Backup Bundle과 Restore
- Snapshot 삭제, 자동 Retention Job과 Secret Export

## 기술 설계

- DB 파일 이름은 모든 Runtime Mode에서 `pangi.sqlite3`로 통일하고 Data Directory 바로 아래에 둔다.
- 기본 Profile은 `journal_mode=DELETE`, Process 1개, `aiosqlite` Connection 1개와 Write Coordinator다.
- 모든 연결에서 Foreign Key, Busy Timeout과 짧은 Transaction을 강제한다.
- Application이 Repository Port와 Unit of Work 경계를 소유하고 SQLite Adapter가 구현한다.
- Runtime 시작은 Migration을 먼저 적용한 뒤 Process Lock과 단일 Connection을 획득한다.
- Unit of Work는 중첩·재사용·이중 완료를 거부하고 명시적으로 Commit되지 않은 작업을 종료·예외·취소 시 Rollback한다.
- Section 14.3은 논리 Schema Catalog다. 각 기능 WBS가 자기 Table의 Domain Model, DB 제약, Migration과 Repository를 같은 변경에서 구현한다.
- Migration은 번호, 이름, Checksum을 불변으로 기록하고 시작 시 Backup 후 Transaction으로 적용한다.
- Destructive Migration은 같은 Release에서 실행하지 않고 최소 두 Minor Version의 Read Compatibility를 둔다.
- DB에는 원문 Prompt/MCP Result/Token을 두지 않고 Redacted Summary, Fingerprint와 Secret Reference만 저장한다.
- DB Snapshot은 SQLite Backup API를 사용하고 임시 파일→무결성 검사→Manifest→원자적 Commit 순서로 생성한다.
- DB Snapshot Manifest는 Hash, 크기, Schema/Migration 이력만 기록하고 절대 경로, Config와 Secret을 기록하지 않는다.
- 기능 데이터 Retention Query는 Table 소유 WBS가 구현하고 전체 Backup/Restore·삭제는 WBS-19가 담당한다.

## 내부 구현 단계

WBS 번호와 문서는 늘리지 않고 아래 실행 단위를 여러 PR로 구현한다.

1. **SQLite 연결과 Migration 기반**: Config, 경로, Process Lock, Connection Profile, Migration Plan/Apply, Checksum, 사전 Snapshot과 Doctor를 만든다.
2. **Runtime Connection과 Unit of Work**: 단일 Runtime Connection, Transaction 상태 전이와 기능 WBS별 Schema 소유권을 만든다.
3. **DB Snapshot·검증 완성**: Migration/Runtime 공통 Snapshot, Manifest, 검증과 운영 진단을 완성하고 Retention 소유권을 정한다.

## 구현 체크리스트

- [x] SQLite Config 검증과 단일 Process Lock을 구현한다.
- [x] Connection Factory, Write Coordinator와 Unit of Work를 구현한다.
- [x] 설계 Table의 기능 WBS별 Migration/Repository 소유권을 정한다.
- [x] 기능 WBS가 주요 Unique/XOR/Foreign Key 제약을 자기 Migration에 선언하도록 규칙을 정한다.
- [x] 기능 Repository가 공유할 Transaction Test Fixture 기반을 만든다.
- [x] Migration Registry, Checksum 검증과 `migrate plan/apply` 기반을 만든다.
- [x] Startup Migration 실패 시 SQLite Runtime 시작을 차단한다.
- [x] DB Snapshot, Manifest와 읽기 전용 검증 기반을 만든다.
- [x] DB Size, Disk Free, `quick_check`, Schema Version을 Doctor에 연결한다.
- [x] Web Health/Ready 연결을 WBS-04 책임으로 명시한다.

## 검증 체크리스트

- [x] 새 DB 생성부터 현재 Package Schema 적용까지 Integration Test를 실행한다.
- [x] 적용된 Migration 파일 변경이 Checksum 오류로 실패하는지 확인한다.
- [x] Unique, Target XOR, Soft Delete와 Foreign Key 검증을 Table 소유 WBS로 이관한다.
- [x] 종료·예외·취소 Rollback과 동시 Transaction 직렬화를 확인한다.
- [x] 실행 중 Snapshot을 격리해 `quick_check`, Hash와 Manifest가 일치하는지 확인한다.
- [x] Snapshot/Manifest에 Secret, Config 본문과 절대 경로가 없는지 검사한다.
- [x] 두 번째 Process와 Network Filesystem Profile을 거부하는지 확인한다.

## 1차 구현 결과

- 기존 Config와 호환되는 `StorageConfig`를 추가하고 Local SQLite URL, `DELETE` Journal과 Busy Timeout을 엄격하게 검증한다.
- `pangi.sqlite3`와 `pangi.lock`을 Runtime 경로 계약에 추가하고 알려진 Network Filesystem, Symlink DB와 두 번째 Process를 거부한다.
- Package Resource SQL을 연속된 Version과 SHA-256 Checksum으로 검증하고 전체 Pending Migration을 하나의 Transaction으로 적용한다.
- 기존 DB에 Pending Migration이 있으면 적용 전에 SQLite Backup API Snapshot과 `quick_check`를 실행한다.
- `pangi migrate plan/apply`와 실제 SQLite Doctor 검사를 연결했다.
- 전체 업무 Table, Repository, Unit of Work, Startup Migration과 Backup Manifest·Retention이 남아 있으므로 WBS 상태는 `진행 중`으로 유지한다.

## 2차 구현 결과

- Framework 의존성이 없는 `UnitOfWork`/`UnitOfWorkFactory` Application Port와 SQLite Adapter를 추가했다.
- Runtime 시작 시 Migration을 먼저 적용하고 Process Lock과 `aiosqlite` Connection 하나를 수명주기 전체에서 소유한다.
- 명시적 Commit, 명시적/자동 Rollback, 예외·취소 정리, 중첩·재사용·이중 완료 거부를 상태 전이로 고정했다.
- 동시 Unit of Work는 Write Coordinator에서 직렬화하고 두 번째 Runtime은 Process Lock에서 거부한다.
- WBS-03은 SQLite 기반만 소유하고 기능 Table, 제약, Migration과 Repository는 해당 기능 WBS가 함께 소유하도록 변경했다.
- Backup Manifest·Retention과 Web Ready 연결이 남아 있으므로 WBS 상태는 `진행 중`으로 유지한다.

## 3차 구현 결과

- Migration 전 Backup과 실행 중 Runtime Snapshot이 같은 SQLite Snapshot Adapter를 사용한다.
- Snapshot과 Canonical JSON Manifest를 `0600`으로 만들고 Hash, 크기, `quick_check`, Schema Version과 Migration 이력을 기록한다.
- 임시 파일과 Hard Link Commit Marker를 사용해 실패·취소 시 불완전한 Snapshot/Manifest가 노출되지 않게 했다.
- Runtime Snapshot은 Write Coordinator에서 활성 Unit of Work와 직렬화하고 Commit된 상태만 포함한다.
- 읽기 전용 검증이 변조, 경로 이탈, Symlink, Manifest Shape와 Schema 이력 불일치를 거부한다.
- Doctor는 Snapshot 없음·정상·손상·현재 Package 호환성 상태를 구분한다.
- Web Ready는 WBS-04, 기능 데이터 Retention은 Table 소유 WBS, 전체 Backup/Restore·삭제는 WBS-19로 이관했다.
- WBS-03이 소유한 구현·검증 조건을 모두 충족해 상태를 `완료`로 변경한다.

## 완료 조건

- 깨끗한 Host에서 DB 생성과 Migration이 자동으로 완료된다.
- Migration Checksum 변경과 적용 실패는 SQLite Runtime 시작을 차단하고 WBS-04 Ready 판정의 실패 입력이 된다.
- 공유 Unit of Work가 기능 Repository의 변경을 원자적으로 Commit/Rollback할 수 있다.
- 기능 Table과 Repository는 소유 WBS에서 Domain 계약과 함께 추가된다.
- Legacy DB 없이 Greenfield Schema로 실행하며 Secret 원문을 저장하지 않는다.

## 미결정 사항

- WAL Profile을 별도 지원할 시점
- PostgreSQL 전환 ADR을 시작할 실제 SLO 임계값
