CREATE TABLE tool_policies (
    stable_tool_id TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    policy_version TEXT NOT NULL CHECK (length(policy_version) BETWEEN 1 AND 120),
    effect TEXT NOT NULL CHECK (effect IN ('allow', 'deny')),
    permission TEXT NOT NULL CHECK (permission IN ('read', 'write', 'destructive')),
    approval TEXT NOT NULL CHECK (approval IN ('none', 'user', 'admin')),
    schema_fingerprint TEXT NOT NULL CHECK (
        length(schema_fingerprint) = 64
        AND schema_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    max_calls_per_run INTEGER NOT NULL CHECK (max_calls_per_run >= 0),
    max_argument_bytes INTEGER NOT NULL CHECK (max_argument_bytes >= 1),
    timeout_seconds INTEGER NOT NULL CHECK (timeout_seconds BETWEEN 1 AND 120),
    max_result_bytes INTEGER NOT NULL CHECK (max_result_bytes >= 1),
    policy_fingerprint TEXT NOT NULL CHECK (
        length(policy_fingerprint) = 64
        AND policy_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    state TEXT NOT NULL CHECK (state IN ('draft', 'active', 'retired')),
    created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
    updated_at TEXT NOT NULL CHECK (julianday(updated_at) IS NOT NULL),
    PRIMARY KEY (stable_tool_id, policy_version),
    UNIQUE (policy_fingerprint),
    FOREIGN KEY (connection_id, stable_tool_id)
        REFERENCES connection_tools(connection_id, stable_tool_id) ON DELETE RESTRICT,
    CHECK (julianday(updated_at) >= julianday(created_at))
);

CREATE UNIQUE INDEX tool_policies_one_active_tool_idx
ON tool_policies(stable_tool_id)
WHERE state = 'active';

CREATE INDEX tool_policies_connection_state_idx
ON tool_policies(connection_id, state, stable_tool_id, policy_version);

CREATE TRIGGER tool_policies_insert_as_draft
BEFORE INSERT ON tool_policies
WHEN NEW.state != 'draft'
BEGIN
    SELECT RAISE(ABORT, 'Tool Policy must be inserted as draft');
END;

CREATE TRIGGER tool_policies_rules_immutable
BEFORE UPDATE OF
    stable_tool_id,
    connection_id,
    policy_version,
    effect,
    permission,
    approval,
    schema_fingerprint,
    max_calls_per_run,
    max_argument_bytes,
    timeout_seconds,
    max_result_bytes,
    policy_fingerprint,
    created_at
ON tool_policies
BEGIN
    SELECT RAISE(ABORT, 'Tool Policy version is immutable');
END;

CREATE TRIGGER tool_policies_state_transition
BEFORE UPDATE OF state, updated_at ON tool_policies
WHEN
    NOT (
        (OLD.state = 'draft' AND NEW.state = 'active')
        OR (OLD.state = 'active' AND NEW.state = 'retired')
    )
    OR NEW.updated_at <= OLD.updated_at
BEGIN
    SELECT RAISE(ABORT, 'Tool Policy state transition is invalid');
END;

CREATE TRIGGER tool_policies_delete_forbidden
BEFORE DELETE ON tool_policies
BEGIN
    SELECT RAISE(ABORT, 'Tool Policy version cannot be deleted');
END;

CREATE TABLE tool_call_budgets (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    stable_tool_id TEXT NOT NULL REFERENCES connection_tools(stable_tool_id) ON DELETE RESTRICT,
    calls_used INTEGER NOT NULL CHECK (calls_used >= 1),
    last_policy_fingerprint TEXT NOT NULL CHECK (
        length(last_policy_fingerprint) = 64
        AND last_policy_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
    updated_at TEXT NOT NULL CHECK (julianday(updated_at) IS NOT NULL),
    PRIMARY KEY (run_id, stable_tool_id),
    CHECK (julianday(updated_at) >= julianday(created_at))
);

CREATE INDEX tool_call_budgets_tool_updated_idx
ON tool_call_budgets(stable_tool_id, updated_at, run_id);

CREATE TRIGGER tool_call_budgets_insert_first_call
BEFORE INSERT ON tool_call_budgets
WHEN NEW.calls_used != 1 OR NEW.updated_at != NEW.created_at
BEGIN
    SELECT RAISE(ABORT, 'Tool Call Budget must begin with one consumed call');
END;

CREATE TRIGGER tool_call_budgets_monotonic_update
BEFORE UPDATE ON tool_call_budgets
WHEN
    NEW.run_id IS NOT OLD.run_id
    OR NEW.stable_tool_id IS NOT OLD.stable_tool_id
    OR NEW.created_at IS NOT OLD.created_at
    OR NEW.calls_used != OLD.calls_used + 1
    OR NEW.updated_at <= OLD.updated_at
BEGIN
    SELECT RAISE(ABORT, 'Tool Call Budget update must consume exactly one call');
END;
