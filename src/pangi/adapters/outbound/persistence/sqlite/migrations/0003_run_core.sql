CREATE TABLE runs (
    id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 16 AND 64),
    request_id TEXT NOT NULL UNIQUE CHECK (length(request_id) BETWEEN 8 AND 80),
    principal_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    trigger TEXT NOT NULL CHECK (trigger IN ('slack', 'api', 'dashboard', 'scheduler', 'eval')),
    state TEXT NOT NULL CHECK (
        state IN (
            'received', 'blocked', 'planning', 'queued', 'running',
            'composing', 'completed', 'failed', 'cancelled', 'interrupted'
        )
    ),
    mode TEXT CHECK (mode IS NULL OR mode IN ('direct', 'delegate', 'skill')),
    skill_version_id TEXT CHECK (
        skill_version_id IS NULL OR length(skill_version_id) BETWEEN 16 AND 64
    ),
    request_text TEXT NOT NULL CHECK (length(trim(request_text)) BETWEEN 1 AND 100000),
    thread_key TEXT CHECK (thread_key IS NULL OR length(thread_key) BETWEEN 1 AND 255),
    explicit_skill TEXT CHECK (
        explicit_skill IS NULL OR length(explicit_skill) BETWEEN 1 AND 255
    ),
    schedule_id TEXT CHECK (schedule_id IS NULL OR length(schedule_id) BETWEEN 16 AND 64),
    attachments_json TEXT NOT NULL DEFAULT '[]' CHECK (
        json_valid(attachments_json) AND json_type(attachments_json) = 'array'
    ),
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 255),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    worker_id TEXT CHECK (worker_id IS NULL OR length(worker_id) BETWEEN 16 AND 64),
    lease_expires_at TEXT,
    heartbeat_at TEXT,
    warnings_json TEXT NOT NULL DEFAULT '[]' CHECK (
        json_valid(warnings_json) AND json_type(warnings_json) = 'array'
    ),
    error_code TEXT CHECK (error_code IS NULL OR length(error_code) BETWEEN 1 AND 120),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    queued_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    CHECK (
        state != 'running'
        OR (worker_id IS NOT NULL AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL)
    ),
    CHECK (
        state NOT IN ('completed', 'failed', 'cancelled')
        OR finished_at IS NOT NULL
    )
);

CREATE INDEX runs_queue_claim_idx ON runs(state, queued_at, created_at, id);

CREATE INDEX runs_principal_created_idx ON runs(principal_id, created_at DESC, id DESC);

CREATE TABLE run_steps (
    id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 16 AND 64),
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    node_id TEXT NOT NULL CHECK (length(node_id) BETWEEN 1 AND 255),
    type TEXT NOT NULL CHECK (length(type) BETWEEN 1 AND 120),
    state TEXT NOT NULL CHECK (
        state IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted')
    ),
    requirement TEXT NOT NULL CHECK (requirement IN ('required', 'optional')),
    idempotent INTEGER NOT NULL CHECK (idempotent IN (0, 1)),
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    depends_on_json TEXT NOT NULL DEFAULT '[]' CHECK (
        json_valid(depends_on_json) AND json_type(depends_on_json) = 'array'
    ),
    error_code TEXT CHECK (error_code IS NULL OR length(error_code) BETWEEN 1 AND 120),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE (run_id, node_id, attempt),
    UNIQUE (run_id, id),
    CHECK (
        state NOT IN ('completed', 'failed', 'cancelled')
        OR finished_at IS NOT NULL
    )
);

CREATE INDEX run_steps_run_state_idx ON run_steps(run_id, state, node_id, attempt);

CREATE TABLE run_events (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    event_index INTEGER NOT NULL CHECK (event_index >= 1),
    type TEXT NOT NULL CHECK (length(type) BETWEEN 3 AND 120 AND instr(type, '.') > 1),
    visibility TEXT NOT NULL CHECK (visibility IN ('public', 'admin', 'internal')),
    step_id TEXT,
    message TEXT CHECK (message IS NULL OR length(message) <= 2000),
    attributes_json TEXT NOT NULL DEFAULT '{}' CHECK (
        json_valid(attributes_json) AND json_type(attributes_json) = 'object'
    ),
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, event_index),
    FOREIGN KEY (run_id, step_id) REFERENCES run_steps(run_id, id) ON DELETE CASCADE
);

CREATE INDEX run_events_step_idx ON run_events(run_id, step_id, event_index);

CREATE TABLE api_idempotency_records (
    principal_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    route_key TEXT NOT NULL CHECK (length(route_key) BETWEEN 1 AND 120),
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 255),
    request_fingerprint TEXT NOT NULL CHECK (
        length(request_fingerprint) = 64
        AND request_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    response_json TEXT CHECK (response_json IS NULL OR json_valid(response_json)),
    state TEXT NOT NULL CHECK (state IN ('processing', 'completed', 'failed')),
    run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (principal_id, route_key, idempotency_key),
    CHECK (
        (state = 'processing' AND response_json IS NULL)
        OR (state IN ('completed', 'failed') AND response_json IS NOT NULL)
    ),
    CHECK (expires_at > created_at)
);

CREATE INDEX api_idempotency_expiry_idx ON api_idempotency_records(expires_at);

CREATE INDEX api_idempotency_run_idx ON api_idempotency_records(run_id);
