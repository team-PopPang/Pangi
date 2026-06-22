import asyncio

from pangi.infra.slack.client import SlackWebClient
from pangi.infra.slack.markdown_to_slack import markdown_to_slack


def test_markdown_to_slack_formats_common_markdown_for_slack_readability():
    markdown = """\
# 결론
본문 **강조** [문서](https://example.com)
* 첫 항목
`**코드** [링크](x)`"""

    assert markdown_to_slack(markdown) == """\
*결론*

본문 *강조* <https://example.com|문서>
- 첫 항목
`**코드** [링크](x)`"""


def test_markdown_to_slack_keeps_inline_emphasis_slack_safe_before_korean_suffix():
    markdown = "6월 4일 회의는 *팝업 제보하기 기능과 관리자 페이지 범위를 정리한 회의*입니다."

    assert markdown_to_slack(markdown) == "6월 4일 회의는 *팝업 제보하기 기능과 관리자 페이지 범위를 정리한 회의입니다*."


def test_markdown_to_slack_keeps_markdown_bold_slack_safe_before_korean_suffix():
    markdown = "상태는 **확인 필요**입니다."

    assert markdown_to_slack(markdown) == "상태는 *확인 필요입니다*."


def test_markdown_to_slack_preserves_fenced_code_blocks():
    markdown = """\
```swift
# 제목 아님
**강조 아님** [링크 아님](https://example.com)
```
## 다음
![스크린샷](https://example.com/image.png)"""

    assert markdown_to_slack(markdown) == """\
```swift
# 제목 아님
**강조 아님** [링크 아님](https://example.com)
```
*다음*

<https://example.com/image.png|스크린샷>"""


def test_markdown_to_slack_wraps_markdown_tables_in_code_block():
    markdown = """\
| 컬럼 | 설명 |
| --- | --- |
| id | 값 |

마무리"""

    assert markdown_to_slack(markdown) == """\
```
| 컬럼 | 설명 |
| --- | --- |
| id | 값 |
```

마무리"""


def test_slack_web_client_converts_markdown_at_slack_boundary(monkeypatch):
    payloads = []

    def fake_post_json(self, path, payload, ignored_errors=None):
        payloads.append((path, payload, ignored_errors))

    monkeypatch.setattr(SlackWebClient, "_post_json", fake_post_json)

    async def scenario():
        client = SlackWebClient(bot_token="test-token")
        await client.post_message(channel_id="C123", thread_ts="171.1", text="# 제목\n**강조**")

    asyncio.run(scenario())

    assert payloads == [
        (
            "/chat.postMessage",
            {
                "channel": "C123",
                "thread_ts": "171.1",
                "text": "*제목*\n\n*강조*",
            },
            None,
        )
    ]
