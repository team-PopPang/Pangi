CREATE TABLE tool_invocations (
    id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 16 AND 64),
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_id TEXT,
    connection_id TEXT REFERENCES connections(id) ON DELETE RESTRICT,
    stable_tool_id TEXT NOT NULL CHECK (length(stable_tool_id) BETWEEN 1 AND 255),
    policy_version TEXT CHECK (
        policy_version IS NULL OR length(policy_version) BETWEEN 1 AND 120
    ),
    policy_fingerprint TEXT REFERENCES tool_policies(policy_fingerprint)
        ON DELETE RESTRICT CHECK (
            policy_fingerprint IS NULL
            OR (
                length(policy_fingerprint) = 64
                AND policy_fingerprint NOT GLOB '*[^0-9a-f]*'
            )
        ),
    approval_grant_id TEXT REFERENCES tool_approvals(id) ON DELETE RESTRICT,
    arguments_fingerprint TEXT CHECK (
        arguments_fingerprint IS NULL
        OR (
            length(arguments_fingerprint) = 64
            AND arguments_fingerprint NOT GLOB '*[^0-9a-f]*'
        )
    ),
    argument_bytes INTEGER CHECK (argument_bytes IS NULL OR argument_bytes >= 0),
    permission TEXT CHECK (
        permission IS NULL OR permission IN ('read', 'write', 'destructive')
    ),
    calls_used INTEGER CHECK (calls_used IS NULL OR calls_used >= 0),
    timeout_seconds INTEGER CHECK (
        timeout_seconds IS NULL OR timeout_seconds BETWEEN 1 AND 120
    ),
    max_result_bytes INTEGER CHECK (
        max_result_bytes IS NULL OR max_result_bytes >= 1
    ),
    duration_ms INTEGER NOT NULL DEFAULT 0 CHECK (duration_ms >= 0),
    state TEXT NOT NULL CHECK (
        state IN ('denied', 'running', 'completed', 'failed', 'cancelled')
    ),
    error_code TEXT CHECK (error_code IS NULL OR length(error_code) BETWEEN 1 AND 120),
    created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
    finished_at TEXT CHECK (finished_at IS NULL OR julianday(finished_at) IS NOT NULL),
    FOREIGN KEY (run_id, step_id) REFERENCES run_steps(run_id, id) ON DELETE CASCADE,
    CHECK (
        (policy_version IS NULL AND policy_fingerprint IS NULL)
        OR (policy_version IS NOT NULL AND policy_fingerprint IS NOT NULL)
    ),
    CHECK (arguments_fingerprint IS NULL OR argument_bytes IS NOT NULL),
    CHECK (
        finished_at IS NULL OR julianday(finished_at) >= julianday(created_at)
    ),
    CHECK (
        state = 'denied'
        OR (
            connection_id IS NOT NULL
            AND policy_version IS NOT NULL
            AND policy_fingerprint IS NOT NULL
            AND arguments_fingerprint IS NOT NULL
            AND argument_bytes IS NOT NULL
            AND permission IS NOT NULL
            AND calls_used >= 1
            AND timeout_seconds IS NOT NULL
            AND max_result_bytes IS NOT NULL
        )
    ),
    CHECK (
        (state = 'running' AND finished_at IS NULL AND error_code IS NULL AND duration_ms = 0)
        OR (
            state = 'completed'
            AND finished_at IS NOT NULL
            AND error_code IS NULL
        )
        OR (
            state = 'failed'
            AND finished_at IS NOT NULL
            AND error_code = 'tool_execution_failed'
        )
        OR (
            state = 'cancelled'
            AND finished_at IS NOT NULL
            AND error_code = 'tool_execution_cancelled'
        )
        OR (
            state = 'denied'
            AND finished_at IS NOT NULL
            AND error_code IS NOT NULL
            AND duration_ms = 0
        )
    )
);

CREATE UNIQUE INDEX tool_invocations_budget_attempt_idx
ON tool_invocations(run_id, stable_tool_id, calls_used)
WHERE state != 'denied' AND calls_used IS NOT NULL;

CREATE INDEX tool_invocations_run_created_idx
ON tool_invocations(run_id, created_at, id);

CREATE INDEX tool_invocations_tool_state_idx
ON tool_invocations(stable_tool_id, state, created_at);

CREATE TRIGGER tool_invocations_identity_immutable
BEFORE UPDATE OF
    id,
    run_id,
    step_id,
    connection_id,
    stable_tool_id,
    policy_version,
    policy_fingerprint,
    approval_grant_id,
    arguments_fingerprint,
    argument_bytes,
    permission,
    calls_used,
    timeout_seconds,
    max_result_bytes,
    created_at
ON tool_invocations
BEGIN
    SELECT RAISE(ABORT, 'Tool Invocation identity and start metadata are immutable');
END;

CREATE TRIGGER tool_invocations_terminal_transition
BEFORE UPDATE OF state, duration_ms, error_code, finished_at ON tool_invocations
WHEN
    OLD.state != 'running'
    OR NEW.state NOT IN ('completed', 'failed', 'cancelled')
BEGIN
    SELECT RAISE(ABORT, 'Tool Invocation terminal transition is invalid');
END;
