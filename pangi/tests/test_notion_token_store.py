from pathlib import Path

from pangi.infra.notion.token_store import (
    JsonNotionTokenStore,
    NotionOAuthClientRegistration,
    NotionOAuthConnection,
    NotionOAuthMetadata,
    NotionOAuthPendingState,
    NotionOAuthTokenSet,
)


def test_json_notion_token_store_round_trips_connection(tmp_path):
    store = JsonNotionTokenStore(tmp_path / "notion" / "oauth.json")
    connection = NotionOAuthConnection(
        metadata=NotionOAuthMetadata(
            authorization_endpoint="https://auth.example.com/authorize",
            token_endpoint="https://auth.example.com/token",
            registration_endpoint="https://auth.example.com/register",
        ),
        client=NotionOAuthClientRegistration(client_id="client-123", client_secret=None),
        tokens=NotionOAuthTokenSet(
            access_token="access-token",
            refresh_token="refresh-token",
            expires_at=123456,
        ),
        pending=NotionOAuthPendingState(
            state="state",
            code_verifier="verifier",
            redirect_uri="https://pangi.example.com/pangi-admin/notion/callback",
            created_at=123,
        ),
    )

    store.save(connection)

    assert store.load() == connection
    assert store.path == Path(tmp_path / "notion" / "oauth.json")
    assert oct(store.path.stat().st_mode & 0o777) == "0o600"


def test_json_notion_token_store_clear_removes_file(tmp_path):
    store = JsonNotionTokenStore(tmp_path / "oauth.json")
    store.save(
        NotionOAuthConnection(
            metadata=NotionOAuthMetadata(
                authorization_endpoint="https://auth.example.com/authorize",
                token_endpoint="https://auth.example.com/token",
            ),
            client=NotionOAuthClientRegistration(client_id="client-123"),
            tokens=None,
        )
    )

    store.clear()

    assert store.load() is None
