# WBS-13 Memory

## 요약

사용자나 Admin이 명시적으로 저장한 짧은 업무 Context를 User/Team/Skill Scope와 적용 조건에 따라 선택하고, 승인된 Revision만 제한된 Prompt Budget 안에 주입한다.

## 목표

- 대화 자동 학습 없이 수동 승인 Memory만 사용한다.
- 소유 범위와 적용 범위를 분리한다.
- Root/Subagent별 Memory Budget과 구체성 우선순위를 강제한다.
- Active Memory 수정은 새 Draft Revision과 Audit를 만든다.

## 선행 작업

- WBS-03
- WBS-04
- WBS-06
- WBS-11

## 설계서 참조

- [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md): Section 15, 16.3, 17.5, 21.6, 23.1~23.4, 24 Phase 4

## 범위

- User/Team/Skill Memory Domain과 Applicability
- Draft/Active/Expired/Disabled/Deleted 상태와 Revision
- Channel/Skill/Domain 조건 선택과 Prompt Budget
- Secret 검사, 만료와 권한 상실 처리
- Memory 목록/편집/Preview/Revision/Audit UI와 API

## 범위 밖

- Vector Embedding, FTS5와 전체 대화 검색
- 자동 Memory Proposal과 자동 학습
- MCP 원문/Secret 저장
- Memory가 System/Tool Policy를 재정의하는 기능

## 기술 설계

- `owner_scope`는 수정 권한, `applies_to`는 전체/Channel/Skill/Domain 주입 조건을 나타낸다.
- Active이고 Principal이 사용할 수 있으며 현재 Context 조건과 일치하는 Memory만 선택한다.
- 선택 우선순위는 Skill+Channel→Skill→Channel→전체이며 Root Summary 2KB, 전체 Run 8KB Budget을 적용한다.
- 권한 확대나 System Policy 충돌 문장은 제외하고 선택/제외 이유를 Trace에 남긴다.
- Active Content/Scope 변경은 `If-Match`/Revision을 검사해 새 Draft를 만들고 승인 후 교체한다.
- Secret Pattern은 저장 전에 거부하며 삭제는 Soft Delete와 Audit를 사용한다.
- UI Preview는 어떤 Prompt 단계에 어떤 Summary가 들어가는지 안전하게 표시한다.

## 구현 체크리스트

- [ ] Memory Item/Applicability/Revision Domain Model을 구현한다.
- [ ] Scope/Role/State/Expiry 기반 Repository Query를 구현한다.
- [ ] 구체성 정렬, Root/Run Budget과 Content Sanitizer를 구현한다.
- [ ] Memory 선택/제외 Trace Event를 구현한다.
- [ ] Draft 생성, 승인, 비활성화, 만료와 Soft Delete Use Case를 구현한다.
- [ ] `If-Match` 수정과 Active→Draft Revision 경로를 구현한다.
- [ ] Memory API, Scope Tab, Card, Editor와 Prompt Preview를 구현한다.
- [ ] 생성/수정/활성/비활성/삭제 Actor와 시각 Audit를 연결한다.

## 검증 체크리스트

- [ ] 전체/Channel/Skill/Domain 조합별 선택 Matrix를 테스트한다.
- [ ] 다른 사용자/팀의 Memory와 탈퇴 Channel Memory가 주입되지 않는지 확인한다.
- [ ] Root 2KB/Run 8KB Budget과 안정된 선택 순서를 검증한다.
- [ ] System/Tool 권한을 확대하는 Memory 문장이 제외되는지 확인한다.
- [ ] 동시 수정의 오래된 `If-Match`가 실패하는지 확인한다.
- [ ] Active 수정이 새 Draft Revision과 Audit를 만드는지 E2E로 확인한다.
- [ ] Memory Content, API와 Preview에 Secret이 없는지 검사한다.

## 완료 조건

- 전체/Channel/Skill 적용 범위에 맞는 Memory만 해당 요청에 주입된다.
- 생성·수정 시각과 Revision을 UI에서 확인할 수 있다.
- Active 수정은 승인 전 기존 Version을 덮어쓰지 않는다.
- 자동 학습, Vector 검색과 Secret 저장이 존재하지 않는다.

## 미결정 사항

- Instance당 Memory 수/크기의 초기 Hard Limit
- Team Memory 승인자 정책
- 5,000개 초과 시 검색 전략 ADR의 시작 조건
