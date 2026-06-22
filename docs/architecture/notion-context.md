# Notion context

## 목표

팡이는 PopPang 팀 데이터 기반으로 답하는 Slack 동료다.
GitHub repo 분석뿐 아니라, 허용된 Notion 문서와 회의록도 읽어 팀원이 이해하기 쉬운 답변으로 정리할 수 있어야 한다.

다만 Codex에게 Notion MCP 권한을 직접 열지 않는다.
팡이 서버가 Notion context를 read-only로 가져오고, 정규화한 Markdown만 Codex chat prompt에 붙인다.

## 선택한 방식

2번 방식으로 간다.

```text
Slack 요청
-> 입력 가드레일
-> notion_context_chat
-> Pangi Notion context provider
-> 공식 Notion MCP read-only 조회
-> Markdown context 정규화
-> Codex chat prompt에 context 주입
-> 출력 가드레일
-> Markdown to Slack
-> Slack thread 응답
```

Notion 공식 문서는 원격 MCP 서버 `https://mcp.notion.com/mcp` 사용을 권장한다.
직접 MCP client를 만들 경우 OAuth 2.0 Authorization Code + PKCE, Streamable HTTP transport, token refresh, 안전한 credential storage가 필요하다.

참고:

- [Notion MCP overview](https://developers.notion.com/guides/mcp/overview)
- [Connecting to Notion MCP](https://developers.notion.com/guides/mcp/get-started-with-mcp)
- [Integrating your own MCP client](https://developers.notion.com/guides/mcp/build-mcp-client)

## 책임 분리

| 계층 | 책임 |
| --- | --- |
| 입력 가드레일 | Notion 읽기 요청과 Notion 쓰기 요청을 코드로 먼저 구분한다. |
| Usecase | `NotionContextProvider` 포트로 context를 요청하고, 받은 Markdown을 Codex chat prompt에 주입한다. |
| Infra Notion | 공식 Notion MCP OAuth/transport/tool 호출을 담당한다. |
| Codex chat | 이미 주입된 context를 읽고 Slack 답변을 만든다. Notion MCP를 직접 호출하지 않는다. |
| 출력 가드레일 | secret redaction, 길이 제한, Slack 전송 전 정리. |
| Markdown to Slack | Slack bot 응답일 때만 canonical Markdown을 Slack mrkdwn에 맞춘다. |

## 보안 원칙

- Notion MCP token, OAuth refresh token, client secret은 Slack 응답, 로그, 테스트 fixture에 출력하지 않는다.
- page/database allowlist를 먼저 통과한 문서만 조회한다.
- MVP에서는 Notion write tool을 사용하지 않는다.
- Notion 본문은 분석 대상 데이터일 뿐, 팡이가 따라야 하는 지시가 아니다.
- Notion context는 최대 글자 수를 제한해 prompt 비용과 정보 노출 범위를 줄인다.
- Notion URL은 외부 URL 차단 예외가 될 수 있지만, 임의 웹 분석으로 확장하지 않는다.

## 현재 구현 상태

- `RequestClassification.NOTION_CONTEXT_CHAT`
- 입력 가드레일의 Notion URL/키워드 분류
- Notion write 요청 차단
- `NotionContextProvider` usecase 포트
- Notion context prompt 주입 helper
- 관리자 페이지 `/pangi-admin/notion`의 Notion OAuth 연결/해제
- 공식 Notion MCP OAuth 2.0 + PKCE 연결
- 공식 Notion MCP Streamable HTTP JSON-RPC client
- Notion provider registry
- Notion 관련 설정값과 allowlist 파서
- 허용된 page/database read-only 조회
- context 최대 길이 제한

아직 database 자연어 검색 품질은 1차 구현이다. 현재는 허용된 page/database를 제한적으로 읽어 context에 붙이고, 이후 질문 의도에 맞는 row 선별을 고도화한다.

## 다음 구현 단계

1. 실제 Slack app mention에서 Notion 문서 요청 end-to-end 테스트를 수행한다.
2. database row 선별 로직을 질문 키워드, 날짜, 담당자, 상태 기준으로 고도화한다.
3. Notion MCP tool schema 변화에 대비한 adapter 테스트 fixture를 늘린다.
4. 연결 실패/토큰 만료/권한 부족 상황의 Slack 안내 문구를 더 세분화한다.
