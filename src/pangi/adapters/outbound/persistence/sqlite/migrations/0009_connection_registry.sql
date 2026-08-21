CREATE TABLE connections (
    id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 120),
    kind TEXT NOT NULL CHECK (length(kind) BETWEEN 1 AND 120),
    display_name TEXT NOT NULL CHECK (length(trim(display_name)) BETWEEN 1 AND 120),
    display_qualifier TEXT CHECK (
        display_qualifier IS NULL
        OR length(trim(display_qualifier)) BETWEEN 1 AND 120
    ),
    scope TEXT NOT NULL CHECK (scope IN ('user', 'instance')),
    owner_user_id TEXT REFERENCES users(id) ON DELETE RESTRICT,
    transport TEXT NOT NULL CHECK (transport IN ('stdio', 'streamable_http')),
    auth_type TEXT NOT NULL CHECK (auth_type IN ('none', 'oauth', 'bearer', 'environment')),
    state TEXT NOT NULL CHECK (
        state IN ('disconnected', 'connecting', 'connected', 'degraded', 'error')
    ),
    config_json TEXT NOT NULL CHECK (
        json_valid(config_json)
        AND json_type(config_json) = 'object'
        AND length(config_json) BETWEEN 2 AND 300000
        AND json_type(config_json, '$.schema_version') = 'integer'
        AND json_extract(config_json, '$.schema_version') = 1
        AND COALESCE(
            (
                transport = 'stdio'
                AND json_type(config_json, '$.endpoint') = 'null'
                AND json_type(config_json, '$.command') = 'text'
                AND length(trim(json_extract(config_json, '$.command'))) BETWEEN 1 AND 4096
                AND json_type(config_json, '$.args') = 'array'
                AND json_array_length(json_extract(config_json, '$.args')) <= 64
            )
            OR (
                transport = 'streamable_http'
                AND json_type(config_json, '$.endpoint') = 'text'
                AND length(trim(json_extract(config_json, '$.endpoint'))) BETWEEN 1 AND 2048
                AND json_type(config_json, '$.command') = 'null'
                AND json_type(config_json, '$.args') = 'array'
                AND json_array_length(json_extract(config_json, '$.args')) = 0
            ),
            0
        )
    ),
    secret_ref TEXT CHECK (secret_ref IS NULL OR length(secret_ref) BETWEEN 1 AND 1024),
    connected_at TEXT CHECK (connected_at IS NULL OR julianday(connected_at) IS NOT NULL),
    last_checked_at TEXT CHECK (
        last_checked_at IS NULL OR julianday(last_checked_at) IS NOT NULL
    ),
    last_error_code TEXT CHECK (
        last_error_code IS NULL OR length(last_error_code) BETWEEN 1 AND 120
    ),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
    updated_at TEXT NOT NULL CHECK (julianday(updated_at) IS NOT NULL),
    CHECK (
        (scope = 'user' AND owner_user_id IS NOT NULL)
        OR (scope = 'instance' AND owner_user_id IS NULL)
    ),
    CHECK (
        (auth_type = 'environment' AND transport = 'stdio')
        OR (auth_type IN ('oauth', 'bearer') AND transport = 'streamable_http')
        OR auth_type = 'none'
    ),
    CHECK (auth_type != 'none' OR secret_ref IS NULL),
    CHECK (
        (state IN ('connected', 'degraded') AND connected_at IS NOT NULL)
        OR state IN ('disconnected', 'connecting', 'error')
    ),
    CHECK (
        (state IN ('degraded', 'error') AND last_error_code IS NOT NULL)
        OR (state IN ('disconnected', 'connecting', 'connected') AND last_error_code IS NULL)
    ),
    CHECK (julianday(updated_at) >= julianday(created_at)),
    CHECK (
        connected_at IS NULL
        OR (
            julianday(connected_at) >= julianday(created_at)
            AND julianday(connected_at) <= julianday(updated_at)
        )
    ),
    CHECK (
        last_checked_at IS NULL
        OR (
            julianday(last_checked_at) >= julianday(created_at)
            AND julianday(last_checked_at) <= julianday(updated_at)
        )
    ),
    CHECK (
        connected_at IS NULL
        OR last_checked_at IS NULL
        OR julianday(last_checked_at) >= julianday(connected_at)
    )
);

CREATE INDEX connections_scope_owner_state_idx
ON connections(scope, owner_user_id, state, updated_at);

CREATE INDEX connections_state_updated_idx
ON connections(state, updated_at, id);

CREATE TRIGGER connections_config_shape_insert
BEFORE INSERT ON connections
WHEN
    (SELECT COUNT(*) FROM json_each(NEW.config_json)) != 4
    OR EXISTS (
        SELECT 1 FROM json_each(NEW.config_json)
        WHERE key NOT IN ('schema_version', 'endpoint', 'command', 'args')
    )
    OR EXISTS (
        SELECT 1 FROM json_each(NEW.config_json, '$.args')
        WHERE type != 'text' OR length(trim(value)) NOT BETWEEN 1 AND 4096
    )
BEGIN
    SELECT RAISE(ABORT, 'Connection config shape is invalid');
END;

CREATE TRIGGER connections_config_shape_update
BEFORE UPDATE OF config_json, transport ON connections
WHEN
    (SELECT COUNT(*) FROM json_each(NEW.config_json)) != 4
    OR EXISTS (
        SELECT 1 FROM json_each(NEW.config_json)
        WHERE key NOT IN ('schema_version', 'endpoint', 'command', 'args')
    )
    OR EXISTS (
        SELECT 1 FROM json_each(NEW.config_json, '$.args')
        WHERE type != 'text' OR length(trim(value)) NOT BETWEEN 1 AND 4096
    )
BEGIN
    SELECT RAISE(ABORT, 'Connection config shape is invalid');
END;

CREATE TRIGGER connections_identity_immutable
BEFORE UPDATE OF id, scope, owner_user_id, created_at ON connections
WHEN
    OLD.id IS NOT NEW.id
    OR OLD.scope IS NOT NEW.scope
    OR OLD.owner_user_id IS NOT NEW.owner_user_id
    OR OLD.created_at IS NOT NEW.created_at
BEGIN
    SELECT RAISE(ABORT, 'Connection identity is immutable');
END;

CREATE TRIGGER connections_revision_increment
BEFORE UPDATE ON connections
WHEN NEW.revision != OLD.revision + 1
BEGIN
    SELECT RAISE(ABORT, 'Connection revision must increment by one');
END;

CREATE TABLE connection_tools (
    stable_tool_id TEXT PRIMARY KEY CHECK (length(stable_tool_id) BETWEEN 1 AND 120),
    connection_id TEXT NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
    remote_name TEXT NOT NULL CHECK (length(remote_name) BETWEEN 1 AND 1024),
    permission TEXT NOT NULL CHECK (permission IN ('read', 'write', 'destructive')),
    schema_json TEXT NOT NULL CHECK (
        json_valid(schema_json)
        AND json_type(schema_json) = 'object'
        AND length(CAST(schema_json AS BLOB)) BETWEEN 2 AND 1000000
    ),
    schema_fingerprint TEXT NOT NULL CHECK (
        length(schema_fingerprint) = 64
        AND schema_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    state TEXT NOT NULL CHECK (state IN ('new', 'active', 'changed', 'unavailable')),
    discovered_at TEXT NOT NULL CHECK (julianday(discovered_at) IS NOT NULL),
    UNIQUE (connection_id, stable_tool_id)
);

CREATE INDEX connection_tools_connection_state_idx
ON connection_tools(connection_id, state, discovered_at, stable_tool_id);

CREATE TRIGGER connection_tools_identity_immutable
BEFORE UPDATE OF stable_tool_id, connection_id ON connection_tools
WHEN
    OLD.stable_tool_id IS NOT NEW.stable_tool_id
    OR OLD.connection_id IS NOT NEW.connection_id
BEGIN
    SELECT RAISE(ABORT, 'Connection Tool identity is immutable');
END;

CREATE TRIGGER connection_tools_discovery_monotonic
BEFORE UPDATE ON connection_tools
WHEN NEW.discovered_at <= OLD.discovered_at
BEGIN
    SELECT RAISE(ABORT, 'Connection Tool discovery time must increase');
END;
