# WBS-19 Package·운영·Upgrade

## 요약

Backend와 빌드된 Admin UI를 하나의 wheel로 배포하고, Service 설치, 진단, Backup/Restore, Upgrade/Rollback/Uninstall/Purge와 Release CI를 안전하게 완성한다.

## 목표

- 새 Host에서 `uv tool` 또는 `pipx` 설치 후 일관된 첫 실행을 제공한다.
- 운영 Host에 Node.js 없이 Dashboard를 제공한다.
- Update 전에 Drain/Backup/호환성을 확인하고 실패 시 복구한다.
- Uninstall은 데이터를 보존하고 Purge만 명시적 검증 뒤 제거한다.

## 선행 작업

- WBS-01~18

## 설계서 참조

- [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md): Section 14.5~14.6, 18, 19, 20, 22.4, 23.3~23.6, 24 Phase 7, 26

## 범위

- Optional Extra/Capability Pack Packaging과 정적 UI 포함
- Wheel Build, Hash/SBOM/Manifest Fingerprint와 Install Smoke
- systemd User/LaunchAgent Service Lifecycle
- `doctor`, Capability Doctor와 Health 운영 계약 완성
- Backup/List/Verify/Restore와 Retention
- Upgrade Check/Drain/Backup/Migration/Smoke/Ready/Rollback
- Uninstall Data 보존과 검증 Backup 기반 Purge
- 단일 Replica Container Image

## 범위 밖

- Kubernetes/다중 Replica와 외부 Queue
- Network Filesystem SQLite
- 알 수 없는 Package Manager/Dirty Checkout의 자동 Update
- 자동 Merge/Release 승인

## 기술 설계

- Release CI는 UI Lock Install→Type/Test→Vite Build→Package Asset 복사→Python Test→Wheel Build→새 환경 Install/Doctor/Smoke 순으로 실행한다.
- Package Environment, Config, Data, Secret Store와 Backup Directory를 별도 경로로 유지한다.
- Service 설치는 생성 파일/실행 계정을 Preview하고 User Mode를 기본으로 한다.
- Upgrade는 Release Signature/Hash와 Compatibility를 검증하고 Drain→Snapshot→Package 교체→Migration→Doctor/Smoke→Ready 순으로 진행한다.
- Migration/Smoke 실패 시 Code 재설치 또는 호환되지 않는 경우 Snapshot Restore를 선택한다.
- Uninstall은 Service/Package만 제거하고 경로를 출력한다. Purge는 정확한 대상과 최신 검증 Backup을 재확인한다.
- Container는 Local Volume, 외부 Secret과 Replica 1개를 강제한다.

## 구현 체크리스트

- [ ] Optional Extra와 Capability Pack Entry Point Packaging을 구성한다.
- [ ] Vite Build Asset을 wheel Package Data에 포함한다.
- [ ] Wheel Hash, SBOM과 Built-in/Pack Manifest Fingerprint를 생성한다.
- [ ] 새 환경 `standard`/`ab180-parity` Install Smoke Job을 만든다.
- [ ] systemd User/LaunchAgent Install/Start/Stop/Logs/Uninstall을 구현한다.
- [ ] Backup Create/List/Verify/Restore와 Manifest를 구현한다.
- [ ] Upgrade Check, Drain, Backup, Package 교체, Migration, Doctor/Smoke와 Ready를 구현한다.
- [ ] Rollback Compatibility와 Snapshot Restore를 구현한다.
- [ ] Uninstall 보존과 안전한 Purge 확인 절차를 구현한다.
- [ ] 단일 Replica Container Build/Health/Volume 계약을 추가한다.

## 검증 체크리스트

- [ ] Linux/macOS 깨끗한 환경에서 wheel 설치와 첫 실행을 확인한다.
- [ ] 운영 환경에 Node.js 없이 Dashboard Asset이 제공되는지 확인한다.
- [ ] Service Install/Restart/Log/Uninstall을 OS별 Fixture에서 검증한다.
- [ ] 실행 중 Backup과 Restore 뒤 DB/Config/Skill Manifest 무결성을 확인한다.
- [ ] Migration/Doctor/Smoke 실패 시 직전 호환 Version으로 복구되는지 확인한다.
- [ ] Upgrade가 사용자 Skill, Runtime Data와 Secret을 덮어쓰지 않는지 확인한다.
- [ ] Uninstall 뒤 Data/Config/Secret/Backup이 남고 Purge가 잘못된 경로를 거부하는지 확인한다.
- [ ] Container Replica/Volume/Network Filesystem 정책을 검증한다.

## 완료 조건

- 새 Host에서 wheel 하나와 문서화된 CLI 흐름으로 실행할 수 있다.
- Backup, Upgrade와 Rollback을 E2E로 통과한다.
- `uninstall`은 사용자 데이터를 보존하고 `purge`만 검증 뒤 제거한다.
- Release Artifact가 Hash/SBOM/Manifest Fingerprint와 Smoke 결과를 가진다.

## 미결정 사항

- 공개 PyPI 또는 Private Registry
- Stable/Beta Release Channel 배포 방식
- Linux/macOS CI Runner Matrix와 지원 종료 정책
