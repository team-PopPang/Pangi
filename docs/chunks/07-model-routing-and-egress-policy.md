# WBS-07 Model Routing과 Egress Policy

## 요약

Root, Subagent, Skill, Eval이 사용하는 모델 호출을 Provider Adapter 뒤로 모으고, 데이터 분류·목적·Region·Retention에 따라 호출 전 Egress Policy를 결정한다.

## 목표

- Provider 세부사항을 Core 계약에서 분리한다.
- 모든 모델 호출 전에 Data Class와 Source Kind를 계산한다.
- 허용되는 Provider/Model/Region/목적 조합만 실행한다.
- Logical Call과 Network Request Retry를 분리해 기록한다.

## 선행 작업

- WBS-02
- WBS-03
- WBS-06

## 설계서 참조

- [Pangi 재설계 구현 설계서](../pangi-rebuild-implementation-design.md): Section 0.1, 8.5, 18.3~18.4, 21.1, 22.1, 23.2, 26

## 범위

- Model Provider Port와 OpenAI/Bedrock 선택 Adapter 기반
- Model Profile, Request Policy와 Egress Policy
- Data Classification, Redaction과 Candidate 선택
- 구조화 출력과 Provider Retry 계측
- Model Policy 관리/영향 분석 API 기반

## 범위 밖

- Root Prompt와 Decision 검증
- Subagent Tool Loop
- Semantic Grader의 평가 기준
- 실제 운영 Model 이름의 문서 고정

## 기술 설계

- 모든 호출자는 목적(`orchestration|subagent|skill|eval|red_team`)과 Source Kind를 명시한 Model Request를 만든다.
- Policy Engine은 입력 Source의 가장 높은 Data Class를 합성하고 후보 Provider를 Allowlist로 줄인다.
- Region, Zero-retention, Raw Content 허용 여부와 Redaction 요구를 검사한 뒤 Adapter를 선택한다.
- 허용 후보가 없으면 임의 Provider Fallback 없이 `model_policy_denied`로 실패한다.
- 원문 Prompt 대신 Policy/Fingerprint/Data Class/Redaction Count/Token/Duration을 저장한다.
- WBS-03 Unit of Work 위에서 `model_policies`, `model_invocations`의 Migration, 제약과 Repository를 이 WBS가 소유한다.
- Transport Retry는 같은 Logical Call의 `provider_request_count`만 증가시키며 결과 뒤 Semantic Retry는 허용하지 않는다.
- Policy 변경은 영향받는 Prompt/Skill/Subagent와 필수 Eval Suite를 계산한 뒤 활성화한다.

## 구현 체크리스트

- [ ] Provider Port, 구조화 출력 Request/Response와 Error 계약을 정의한다.
- [ ] Model Profile과 Versioned Egress Policy Domain을 구현한다.
- [ ] Source Kind별 Data Class 합성과 Redaction Pipeline을 구현한다.
- [ ] Provider/Model/Region/Purpose/Retention 후보 필터를 구현한다.
- [ ] OpenAI와 Bedrock Adapter의 선택 설치 Skeleton을 만든다.
- [ ] Logical Call, Provider Request, Token, Duration과 정책 결정 Event를 기록한다.
- [ ] Policy 영향 분석, Eval 실행과 활성화 API를 구현한다.
- [ ] Dashboard에서 Policy, 사용처와 최근 허용/거부 Summary를 조회할 기반을 만든다.

## 검증 체크리스트

- [ ] Data Class/Provider/Region 조합별 Allow/Deny Matrix를 Unit Test로 고정한다.
- [ ] Redaction 전 금지 Field가 Provider Adapter에 도달하지 않는지 확인한다.
- [ ] 허용 후보가 없을 때 Provider 호출이 0건인지 확인한다.
- [ ] Network Retry와 Logical Call 수가 분리되는지 Contract Test를 실행한다.
- [ ] Provider가 잘못된 구조화 출력을 반환할 때 안전하게 실패하는지 확인한다.
- [ ] Policy 변경이 영향 Eval 없이 활성화되지 않는지 확인한다.

## 완료 조건

- 모든 모델 호출이 Versioned Egress Policy를 통과한다.
- 금지된 Data Class/Provider 조합의 Provider 호출이 0건이다.
- Logical Call과 실제 Provider Request 수를 Trace에서 구분한다.
- 원문 Prompt와 금지 데이터가 Invocation 기록에 저장되지 않는다.

## 미결정 사항

- 설치 시 기본 Provider와 Model Profile
- Provider별 Zero-retention Capability Discovery 방식
- 비용·지연 기반 후보 우선순위의 초기값
