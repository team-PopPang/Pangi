# WBS-10 Subagent와 Web Search

## 요약

Root Decision이 선택한 전문 Subagent를 깊이 1과 제한된 Budget 안에서 병렬 실행하고, 외부 Web 문서를 별도 신뢰 경계에서 안전하게 검색·근거화한다.

## 목표

- 등록된 Subagent만 실행하고 재위임을 코드로 거부한다.
- Tool Loop, Turn, 동시성, Result Byte와 Timeout Budget을 강제한다.
- Partial Result와 Evidence를 안정적으로 반환한다.
- Web Search에서 Prompt Injection, SSRF와 Redirect 우회를 차단한다.

## 선행 작업

- WBS-07
- WBS-08
- WBS-09

## 설계서 참조

- [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md): Section 7.5, 9.3~9.7, 9.9, 21.1~21.3, 23.5, 24 Phase 3

## 범위

- Subagent Manifest/Registry와 Built-in 읽기 Subagent
- Bounded Parallel Execution과 Tool Loop
- `AgentResult`, Budget 초과 Partial과 Metric
- Deterministic Reducer용 Result 전달과 Synthesis Subagent
- Web Search 전용 URL/IP/Redirect/MIME/HTML Policy
- Spotlight Envelope와 Citation 정규화

## 범위 밖

- Root 재호출과 재귀 Subagent
- 임의 Browser 자동화나 사설 Network Fetch
- Repository Shell/Write 작업
- Built-in 업무 Skill Workflow

## 기술 설계

- Registry는 설치된 Connection Capability를 만족하는 Subagent만 Root Catalog에 노출한다.
- 기본 동시 실행 3, 깊이 1, Model Turn 2, Tool Call 5를 사용하고 Hard Max를 서버가 강제한다.
- 같은 Tool/Argument 반복, Unknown Tool과 다른 Subagent 호출을 즉시 실패시킨다.
- 마지막 Turn은 `AgentResult` Schema로 끝나며 Budget 소진 시 `partial`과 Warning을 반환한다.
- Web Search는 공개 Web 전용 Model Profile과 `web.search/web.fetch`만 사용한다.
- URL은 Scheme, Credential, DNS 결과, Redirect마다 Public IP인지 검사하고 Byte/Node/MIME/Redirect Limit을 둔다.
- Script/Form/Event Handler/Hidden Text/Control Character를 제거하고 결과를 비신뢰 Envelope로 감싼다.

## 구현 체크리스트

- [ ] Subagent Protocol, Manifest와 Registry를 구현한다.
- [ ] Connection Capability 기반 Registry 노출 Filter를 구현한다.
- [ ] Bounded Parallel Scheduler와 Turn/Tool/Timeout/Byte Budget을 구현한다.
- [ ] 반복 Tool/Argument와 재귀 위임 차단을 구현한다.
- [ ] AgentResult Schema 종료, Partial/Failure와 Step Metric을 구현한다.
- [ ] Synthesis Subagent의 제한된 입력과 출력을 구현한다.
- [ ] Web URL Parser, DNS/Redirect/IP Policy와 Fetch Limit을 구현한다.
- [ ] HTML Sanitizer, Spotlight Envelope와 Evidence URL 정규화를 구현한다.
- [ ] Built-in 읽기 Subagent Manifest와 Fake MCP Fixture를 추가한다.

## 검증 체크리스트

- [ ] Root 1회 뒤 최대 Subagent/동시성/깊이 Limit을 검증한다.
- [ ] Subagent의 재위임과 Unknown/반복 Tool 호출을 차단한다.
- [ ] Timeout/Budget 소진이 Partial Result와 Warning을 반환하는지 확인한다.
- [ ] 서로 다른 완료 순서에서도 Reducer 입력 순서가 재현되는지 확인한다.
- [ ] Loopback, 사설/Link-local/Metadata, IPv6, DNS Rebinding과 Redirect 우회를 차단한다.
- [ ] Prompt Injection, 대형 HTML, 잘못된 MIME와 Citation 누락 Eval을 실행한다.
- [ ] 외부 페이지가 Tool Policy/Model Policy를 변경하지 못하는지 확인한다.

## 완료 조건

- Subagent Depth가 항상 1 이하이고 등록되지 않은 실행이 0건이다.
- Root 1회 뒤 제한된 Subagent 병렬 실행과 Partial Result가 동작한다.
- Web Search의 사설 주소 접근과 외부 지시 실행이 0건이다.
- 모든 Web 결과가 정규화된 Evidence와 함께 반환된다.

## 미결정 사항

- Built-in Subagent별 기본 Model Profile
- Web Fetch의 초기 Byte/HTML Node Limit
- Synthesis 사용을 허용할 구체적인 Request 분류
