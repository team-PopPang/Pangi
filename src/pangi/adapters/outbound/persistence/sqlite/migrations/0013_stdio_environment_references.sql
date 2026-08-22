PRAGMA legacy_alter_table=ON;

ALTER TABLE connections RENAME TO connections_config_v1;

DROP INDEX connections_scope_owner_state_idx;
DROP INDEX connections_state_updated_idx;
DROP TRIGGER connections_config_shape_insert;
DROP TRIGGER connections_config_shape_update;
DROP TRIGGER connections_identity_immutable;
DROP TRIGGER connections_revision_increment;

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
        AND json_extract(config_json, '$.schema_version') = 2
        AND json_type(config_json, '$.env_secret_refs') = 'object'
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

INSERT INTO connections (
    id,
    kind,
    display_name,
    display_qualifier,
    scope,
    owner_user_id,
    transport,
    auth_type,
    state,
    config_json,
    secret_ref,
    connected_at,
    last_checked_at,
    last_error_code,
    revision,
    created_at,
    updated_at
)
SELECT
    id,
    kind,
    display_name,
    display_qualifier,
    scope,
    owner_user_id,
    transport,
    auth_type,
    state,
    json_object(
        'args', json(json_extract(config_json, '$.args')),
        'command', json_extract(config_json, '$.command'),
        'endpoint', json_extract(config_json, '$.endpoint'),
        'env_secret_refs', json('{}'),
        'schema_version', 2
    ),
    secret_ref,
    connected_at,
    last_checked_at,
    last_error_code,
    revision,
    created_at,
    updated_at
FROM connections_config_v1;

DROP TABLE connections_config_v1;

CREATE INDEX connections_scope_owner_state_idx
ON connections(scope, owner_user_id, state, updated_at);

CREATE INDEX connections_state_updated_idx
ON connections(state, updated_at, id);

CREATE TRIGGER connections_config_shape_insert
BEFORE INSERT ON connections
WHEN
    (SELECT COUNT(*) FROM json_each(NEW.config_json)) != 5
    OR EXISTS (
        SELECT 1 FROM json_each(NEW.config_json)
        WHERE key NOT IN (
            'schema_version',
            'endpoint',
            'command',
            'args',
            'env_secret_refs'
        )
    )
    OR EXISTS (
        SELECT 1 FROM json_each(NEW.config_json, '$.args')
        WHERE type != 'text' OR length(trim(value)) NOT BETWEEN 1 AND 4096
    )
    OR (
        SELECT COALESCE(SUM(length(CAST(value AS BLOB))), 0)
        FROM json_each(NEW.config_json, '$.args')
    ) > 65536
    OR (SELECT COUNT(*) FROM json_each(NEW.config_json, '$.env_secret_refs')) > 32
    OR (
        NEW.transport = 'streamable_http'
        AND (SELECT COUNT(*) FROM json_each(NEW.config_json, '$.env_secret_refs')) != 0
    )
    OR EXISTS (
        SELECT 1 FROM json_each(NEW.config_json, '$.env_secret_refs')
        WHERE
            type != 'text'
            OR length(key) NOT BETWEEN 1 AND 128
            OR key GLOB '*[^A-Z0-9_]*'
            OR substr(key, 1, 1) GLOB '[0-9]'
            OR key IN ('PATH', 'PYTHONPATH', 'PYTHONHOME', 'NODE_OPTIONS', 'BASH_ENV', 'ENV')
            OR key GLOB 'LD_*'
            OR key GLOB 'DYLD_*'
            OR length(value) NOT BETWEEN 1 AND 1024
            OR NOT (
                (
                    value GLOB 'secret:v1:keyring:*'
                    AND length(value) BETWEEN 34 AND 146
                    AND substr(value, 19) NOT GLOB '*[^A-Za-z0-9_-]*'
                )
                OR (
                    value GLOB 'secret:v1:file-vault:*'
                    AND length(value) BETWEEN 37 AND 149
                    AND substr(value, 22) NOT GLOB '*[^A-Za-z0-9_-]*'
                )
            )
    )
BEGIN
    SELECT RAISE(ABORT, 'Connection config shape is invalid');
END;

CREATE TRIGGER connections_config_shape_update
BEFORE UPDATE OF config_json, transport ON connections
WHEN
    (SELECT COUNT(*) FROM json_each(NEW.config_json)) != 5
    OR EXISTS (
        SELECT 1 FROM json_each(NEW.config_json)
        WHERE key NOT IN (
            'schema_version',
            'endpoint',
            'command',
            'args',
            'env_secret_refs'
        )
    )
    OR EXISTS (
        SELECT 1 FROM json_each(NEW.config_json, '$.args')
        WHERE type != 'text' OR length(trim(value)) NOT BETWEEN 1 AND 4096
    )
    OR (
        SELECT COALESCE(SUM(length(CAST(value AS BLOB))), 0)
        FROM json_each(NEW.config_json, '$.args')
    ) > 65536
    OR (SELECT COUNT(*) FROM json_each(NEW.config_json, '$.env_secret_refs')) > 32
    OR (
        NEW.transport = 'streamable_http'
        AND (SELECT COUNT(*) FROM json_each(NEW.config_json, '$.env_secret_refs')) != 0
    )
    OR EXISTS (
        SELECT 1 FROM json_each(NEW.config_json, '$.env_secret_refs')
        WHERE
            type != 'text'
            OR length(key) NOT BETWEEN 1 AND 128
            OR key GLOB '*[^A-Z0-9_]*'
            OR substr(key, 1, 1) GLOB '[0-9]'
            OR key IN ('PATH', 'PYTHONPATH', 'PYTHONHOME', 'NODE_OPTIONS', 'BASH_ENV', 'ENV')
            OR key GLOB 'LD_*'
            OR key GLOB 'DYLD_*'
            OR length(value) NOT BETWEEN 1 AND 1024
            OR NOT (
                (
                    value GLOB 'secret:v1:keyring:*'
                    AND length(value) BETWEEN 34 AND 146
                    AND substr(value, 19) NOT GLOB '*[^A-Za-z0-9_-]*'
                )
                OR (
                    value GLOB 'secret:v1:file-vault:*'
                    AND length(value) BETWEEN 37 AND 149
                    AND substr(value, 22) NOT GLOB '*[^A-Za-z0-9_-]*'
                )
            )
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

PRAGMA legacy_alter_table=OFF;
