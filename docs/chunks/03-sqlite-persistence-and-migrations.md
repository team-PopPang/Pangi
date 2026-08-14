# WBS-03 SQLite 영속성과 Migration

## 요약

단일 조직·단일 Host 운영에 필요한 상태를 SQLite에 안전하게 저장하고, 기능별 Repository, Transaction, Migration, Backup과 Retention의 기반을 만든다.

## 목표

- 외부 DB 없이 Run, Connection, Skill, Schedule, Eval과 Audit 상태를 영속화한다.
- 단일 Process/단일 Writer Profile을 코드와 진단으로 강제한다.
- Migration Checksum과 호환성 규칙을 고정한다.
- Secret과 대용량 외부 원문을 DB에 저장하지 않는다.

## 선행 작업

- WBS-01
- WBS-02

## 설계서 참조

- [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md): Section 14, 18.1~18.3, 19.6~19.7, 20, 24 Phase 0

## 범위

- SQLite Connection/Write Coordinator와 Unit of Work
- 설계서 Section 14.3의 Table과 제약
- 기능별 Repository Adapter
- SQL Migration Package Resource와 Checksum
- Backup API 기반 Snapshot, Retention과 안전한 Export 기반
- Health/Doctor의 DB 검사

## 범위 밖

- PostgreSQL/External Queue Adapter
- Vector DB와 Embedding Search
- Legacy SQLite Schema의 자동 Migration
- OAuth Token과 Secret 본문 저장

## 기술 설계

- DB 파일 이름은 모든 Runtime Mode에서 `pangi.sqlite3`로 통일하고 Data Directory 바로 아래에 둔다.
- 기본 Profile은 `journal_mode=DELETE`, Process 1개, `aiosqlite` Connection 1개와 Write Coordinator다.
- 모든 연결에서 Foreign Key, Busy Timeout과 짧은 Transaction을 강제한다.
- Application이 Repository Port와 Unit of Work 경계를 소유하고 SQLite Adapter가 구현한다.
- Migration은 번호, 이름, Checksum을 불변으로 기록하고 시작 시 Backup 후 Transaction으로 적용한다.
- Destructive Migration은 같은 Release에서 실행하지 않고 최소 두 Minor Version의 Read Compatibility를 둔다.
- DB에는 원문 Prompt/MCP Result/Token을 두지 않고 Redacted Summary, Fingerprint와 Secret Reference만 저장한다.
- Backup은 SQLite Backup API를 사용하며 Secret Export는 별도 암호화와 Admin 확인을 요구한다.

## 내부 구현 단계

WBS 번호와 문서는 늘리지 않고 아래 실행 단위를 여러 PR로 구현한다.

1. **SQLite 연결과 Migration 기반**: Config, 경로, Process Lock, Connection Profile, Migration Plan/Apply, Checksum, 사전 Snapshot과 Doctor를 만든다.
2. **Greenfield Schema와 Repository**: 기능군별 Table, DB 제약, Application Repository Port와 Unit of Work를 만든다.
3. **Backup·Retention 완성**: Backup Manifest, 복구 검증, Retention 기반과 운영 진단을 완성한다.

## 구현 체크리스트

- [x] SQLite Config 검증과 단일 Process Lock을 구현한다.
- [ ] Connection Factory, Write Coordinator와 Unit of Work를 구현한다.
- [ ] 설계 Table을 기능별 Migration으로 분리한다.
- [ ] 주요 Unique/XOR/Foreign Key 제약을 DB에도 선언한다.
- [ ] 기능별 Repository Adapter와 Transaction Test Fixture를 만든다.
- [x] Migration Registry, Checksum 검증과 `migrate plan/apply` 기반을 만든다.
- [ ] Startup Migration 실패 시 Ready를 차단한다.
- [ ] Backup Snapshot, Manifest와 Retention Job의 기반을 만든다.
- [x] DB Size, Disk Free, `quick_check`, Schema Version을 Doctor에 연결한다.
- [ ] 같은 SQLite 상태를 Web Health와 Ready 판정에 연결한다.

## 검증 체크리스트

- [ ] 새 DB 생성부터 최신 Schema 적용까지 Integration Test를 실행한다.
- [x] 적용된 Migration 파일 변경이 Checksum 오류로 실패하는지 확인한다.
- [ ] Unique, Target XOR, Soft Delete와 Foreign Key 제약을 테스트한다.
- [ ] Crash/Rollback 상황에서 Transaction 원자성을 확인한다.
- [ ] 실행 중 Snapshot을 복구해 `quick_check`와 Manifest가 일치하는지 확인한다.
- [ ] DB/API/Log에 Secret 원문이 저장되지 않는지 검사한다.
- [x] 두 번째 Process와 Network Filesystem Profile을 거부하는지 확인한다.

## 1차 구현 결과

- 기존 Config와 호환되는 `StorageConfig`를 추가하고 Local SQLite URL, `DELETE` Journal과 Busy Timeout을 엄격하게 검증한다.
- `pangi.sqlite3`와 `pangi.lock`을 Runtime 경로 계약에 추가하고 알려진 Network Filesystem, Symlink DB와 두 번째 Process를 거부한다.
- Package Resource SQL을 연속된 Version과 SHA-256 Checksum으로 검증하고 전체 Pending Migration을 하나의 Transaction으로 적용한다.
- 기존 DB에 Pending Migration이 있으면 적용 전에 SQLite Backup API Snapshot과 `quick_check`를 실행한다.
- `pangi migrate plan/apply`와 실제 SQLite Doctor 검사를 연결했다.
- 전체 업무 Table, Repository, Unit of Work, Startup Migration과 Backup Manifest·Retention이 남아 있으므로 WBS 상태는 `진행 중`으로 유지한다.

## 완료 조건

- 깨끗한 Host에서 DB 생성과 Migration이 자동으로 완료된다.
- Migration Checksum 변경과 실패는 Ready 상태를 차단한다.
- 기능 Repository가 같은 Unit of Work 안에서 원자적으로 Commit/Rollback된다.
- Legacy DB 없이 Greenfield Schema로 실행하며 Secret 원문을 저장하지 않는다.

## 미결정 사항

- 첫 Benchmark 뒤 확정할 Retention Batch 크기
- WAL Profile을 별도 지원할 시점
- PostgreSQL 전환 ADR을 시작할 실제 SLO 임계값
