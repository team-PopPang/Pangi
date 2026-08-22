CREATE TABLE tool_approvals (
    id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 16 AND 64),
    reference_hash TEXT NOT NULL UNIQUE CHECK (
        length(reference_hash) = 64
        AND reference_hash NOT GLOB '*[^0-9a-f]*'
    ),
    subject_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    approver_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    approver_role TEXT NOT NULL CHECK (
        approver_role IN ('member', 'skill_author', 'admin', 'system')
    ),
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    stable_tool_id TEXT NOT NULL REFERENCES connection_tools(stable_tool_id) ON DELETE RESTRICT,
    arguments_fingerprint TEXT NOT NULL CHECK (
        length(arguments_fingerprint) = 64
        AND arguments_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    policy_fingerprint TEXT NOT NULL REFERENCES tool_policies(policy_fingerprint)
        ON DELETE RESTRICT CHECK (
            length(policy_fingerprint) = 64
            AND policy_fingerprint NOT GLOB '*[^0-9a-f]*'
        ),
    approval_requirement TEXT NOT NULL CHECK (
        approval_requirement IN ('user', 'admin')
    ),
    state TEXT NOT NULL CHECK (state IN ('active', 'consumed')),
    issued_at TEXT NOT NULL CHECK (julianday(issued_at) IS NOT NULL),
    expires_at TEXT NOT NULL CHECK (julianday(expires_at) IS NOT NULL),
    consumed_at TEXT CHECK (consumed_at IS NULL OR julianday(consumed_at) IS NOT NULL),
    CHECK (julianday(expires_at) > julianday(issued_at)),
    CHECK (
        (approval_requirement = 'user' AND approver_user_id = subject_user_id)
        OR (approval_requirement = 'admin' AND approver_role = 'admin')
    ),
    CHECK (
        (state = 'active' AND consumed_at IS NULL)
        OR (
            state = 'consumed'
            AND consumed_at IS NOT NULL
            AND julianday(consumed_at) >= julianday(issued_at)
            AND julianday(consumed_at) < julianday(expires_at)
        )
    )
);

CREATE INDEX tool_approvals_run_tool_state_idx
ON tool_approvals(run_id, stable_tool_id, state, expires_at);

CREATE INDEX tool_approvals_subject_state_idx
ON tool_approvals(subject_user_id, state, expires_at);

CREATE TRIGGER tool_approvals_insert_active
BEFORE INSERT ON tool_approvals
WHEN NEW.state != 'active' OR NEW.consumed_at IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Tool Approval must be inserted as active');
END;

CREATE TRIGGER tool_approvals_claims_immutable
BEFORE UPDATE OF
    id,
    reference_hash,
    subject_user_id,
    approver_user_id,
    approver_role,
    run_id,
    stable_tool_id,
    arguments_fingerprint,
    policy_fingerprint,
    approval_requirement,
    issued_at,
    expires_at
ON tool_approvals
BEGIN
    SELECT RAISE(ABORT, 'Tool Approval claims are immutable');
END;

CREATE TRIGGER tool_approvals_single_consumption
BEFORE UPDATE OF state, consumed_at ON tool_approvals
WHEN
    OLD.state != 'active'
    OR OLD.consumed_at IS NOT NULL
    OR NEW.state != 'consumed'
    OR NEW.consumed_at IS NULL
    OR julianday(NEW.consumed_at) < julianday(OLD.issued_at)
    OR julianday(NEW.consumed_at) >= julianday(OLD.expires_at)
BEGIN
    SELECT RAISE(ABORT, 'Tool Approval can be consumed exactly once before expiry');
END;
