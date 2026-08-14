CREATE TABLE users (
    id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 16 AND 64),
    display_name TEXT NOT NULL CHECK (length(trim(display_name)) BETWEEN 1 AND 80),
    role TEXT NOT NULL CHECK (role IN ('member', 'skill_author', 'admin', 'system')),
    status TEXT NOT NULL CHECK (status IN ('active', 'disabled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE auth_identities (
    id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 16 AND 64),
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('local', 'slack', 'reverse_proxy')),
    subject TEXT NOT NULL CHECK (length(subject) BETWEEN 1 AND 255),
    password_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (provider, subject),
    CHECK (
        (provider = 'local' AND password_hash IS NOT NULL AND password_hash LIKE '$argon2id$%')
        OR (provider != 'local' AND password_hash IS NULL)
    )
);

CREATE INDEX auth_identities_user_id_idx ON auth_identities(user_id);

CREATE TABLE auth_sessions (
    id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 16 AND 64),
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE CHECK (length(token_hash) = 64),
    csrf_hash TEXT NOT NULL CHECK (length(csrf_hash) = 64),
    state TEXT NOT NULL CHECK (state IN ('active', 'revoked', 'expired')),
    expires_at TEXT NOT NULL,
    rotated_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX auth_sessions_user_state_idx ON auth_sessions(user_id, state);

CREATE TABLE bootstrap_grants (
    id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 16 AND 64),
    token_hash TEXT NOT NULL UNIQUE CHECK (length(token_hash) = 64),
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    consumed_by_user_id TEXT REFERENCES users(id) ON DELETE RESTRICT,
    revoked_at TEXT,
    created_at TEXT NOT NULL,
    CHECK ((consumed_at IS NULL) = (consumed_by_user_id IS NULL)),
    CHECK (NOT (consumed_at IS NOT NULL AND revoked_at IS NOT NULL))
);

CREATE UNIQUE INDEX bootstrap_grants_one_open_idx
ON bootstrap_grants ((1))
WHERE consumed_at IS NULL AND revoked_at IS NULL;
