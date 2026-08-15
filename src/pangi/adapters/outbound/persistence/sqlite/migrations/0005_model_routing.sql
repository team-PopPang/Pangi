CREATE TABLE model_policies (
    id TEXT NOT NULL CHECK (length(id) BETWEEN 1 AND 120),
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    version TEXT NOT NULL CHECK (length(version) BETWEEN 1 AND 120),
    rules_json TEXT NOT NULL CHECK (
        json_valid(rules_json)
        AND json_type(rules_json) = 'object'
        AND json_extract(rules_json, '$.schema_version') = 1
        AND json_type(rules_json, '$.policy') = 'object'
        AND json_type(rules_json, '$.profiles') = 'array'
        AND json_array_length(json_extract(rules_json, '$.profiles')) >= 1
    ),
    fingerprint TEXT NOT NULL CHECK (
        length(fingerprint) = 64
        AND fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    state TEXT NOT NULL CHECK (state IN ('draft', 'active', 'retired')),
    eval_run_id TEXT CHECK (
        eval_run_id IS NULL OR length(eval_run_id) BETWEEN 16 AND 64
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id, version),
    UNIQUE (name, version),
    UNIQUE (fingerprint)
);

CREATE UNIQUE INDEX model_policies_one_active_name_idx
ON model_policies(name)
WHERE state = 'active';

CREATE INDEX model_policies_state_name_idx
ON model_policies(state, name, version);

CREATE TRIGGER model_policies_active_rules_immutable
BEFORE UPDATE OF id, name, version, rules_json, fingerprint ON model_policies
WHEN OLD.state = 'active'
BEGIN
    SELECT RAISE(ABORT, 'active Model Policy rules are immutable');
END;

CREATE TRIGGER model_policies_active_delete_forbidden
BEFORE DELETE ON model_policies
WHEN OLD.state = 'active'
BEGIN
    SELECT RAISE(ABORT, 'active Model Policy cannot be deleted');
END;

CREATE TABLE model_invocations (
    id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 16 AND 64),
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_id TEXT,
    logical_call_fingerprint TEXT NOT NULL CHECK (
        length(logical_call_fingerprint) = 64
        AND logical_call_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    role TEXT NOT NULL CHECK (
        role IN ('orchestration', 'subagent', 'skill', 'eval', 'red_team')
    ),
    provider TEXT CHECK (provider IS NULL OR length(provider) BETWEEN 1 AND 120),
    model TEXT CHECK (model IS NULL OR length(model) BETWEEN 1 AND 255),
    region TEXT CHECK (region IS NULL OR length(region) BETWEEN 1 AND 120),
    policy_id TEXT CHECK (policy_id IS NULL OR length(policy_id) BETWEEN 1 AND 120),
    policy_version TEXT CHECK (
        policy_version IS NULL OR length(policy_version) BETWEEN 1 AND 120
    ),
    policy_fingerprint TEXT CHECK (
        policy_fingerprint IS NULL
        OR (
            length(policy_fingerprint) = 64
            AND policy_fingerprint NOT GLOB '*[^0-9a-f]*'
        )
    ),
    profile_id TEXT CHECK (profile_id IS NULL OR length(profile_id) BETWEEN 1 AND 120),
    profile_fingerprint TEXT CHECK (
        profile_fingerprint IS NULL
        OR (
            length(profile_fingerprint) = 64
            AND profile_fingerprint NOT GLOB '*[^0-9a-f]*'
        )
    ),
    data_classes_json TEXT NOT NULL CHECK (
        json_valid(data_classes_json)
        AND json_type(data_classes_json) = 'array'
        AND json_array_length(data_classes_json) >= 1
    ),
    source_kinds_json TEXT NOT NULL CHECK (
        json_valid(source_kinds_json)
        AND json_type(source_kinds_json) = 'array'
        AND json_array_length(source_kinds_json) >= 1
    ),
    redaction_count INTEGER CHECK (redaction_count IS NULL OR redaction_count >= 0),
    input_fingerprint TEXT CHECK (
        input_fingerprint IS NULL
        OR (
            length(input_fingerprint) = 64
            AND input_fingerprint NOT GLOB '*[^0-9a-f]*'
        )
    ),
    output_fingerprint TEXT CHECK (
        output_fingerprint IS NULL
        OR (
            length(output_fingerprint) = 64
            AND output_fingerprint NOT GLOB '*[^0-9a-f]*'
        )
    ),
    logical_calls INTEGER NOT NULL DEFAULT 1 CHECK (logical_calls = 1),
    provider_requests INTEGER NOT NULL DEFAULT 0 CHECK (
        provider_requests BETWEEN 0 AND 10
    ),
    input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
    total_tokens INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0),
    duration_ms INTEGER NOT NULL DEFAULT 0 CHECK (duration_ms >= 0),
    provider_latency_ms INTEGER CHECK (
        provider_latency_ms IS NULL OR provider_latency_ms >= 0
    ),
    finish_reason TEXT CHECK (
        finish_reason IS NULL
        OR finish_reason IN ('stop', 'length', 'content_filtered', 'unknown')
    ),
    state TEXT NOT NULL CHECK (state IN ('running', 'completed', 'failed', 'denied')),
    error_code TEXT CHECK (error_code IS NULL OR length(error_code) BETWEEN 1 AND 120),
    created_at TEXT NOT NULL,
    finished_at TEXT,
    FOREIGN KEY (run_id, step_id) REFERENCES run_steps(run_id, id) ON DELETE CASCADE,
    UNIQUE (run_id, logical_call_fingerprint),
    CHECK (
        (policy_id IS NULL AND policy_version IS NULL AND policy_fingerprint IS NULL)
        OR (
            policy_id IS NOT NULL
            AND policy_version IS NOT NULL
            AND policy_fingerprint IS NOT NULL
        )
    ),
    CHECK (
        (profile_id IS NULL AND profile_fingerprint IS NULL AND provider IS NULL AND model IS NULL)
        OR (
            profile_id IS NOT NULL
            AND profile_fingerprint IS NOT NULL
            AND provider IS NOT NULL
            AND model IS NOT NULL
        )
    ),
    CHECK (
        (input_tokens IS NULL AND output_tokens IS NULL AND total_tokens IS NULL)
        OR (
            input_tokens IS NOT NULL
            AND output_tokens IS NOT NULL
            AND total_tokens IS NOT NULL
            AND total_tokens >= input_tokens
            AND total_tokens >= output_tokens
        )
    ),
    CHECK (
        (state = 'running' AND finished_at IS NULL AND error_code IS NULL)
        OR (
            state = 'completed'
            AND finished_at IS NOT NULL
            AND error_code IS NULL
            AND output_fingerprint IS NOT NULL
            AND provider_requests >= 1
        )
        OR (
            state = 'failed'
            AND finished_at IS NOT NULL
            AND error_code IS NOT NULL
            AND provider_requests >= 1
        )
        OR (
            state = 'denied'
            AND finished_at IS NOT NULL
            AND error_code IS NOT NULL
            AND provider_requests = 0
        )
    )
);

CREATE INDEX model_invocations_run_created_idx
ON model_invocations(run_id, created_at, id);

CREATE INDEX model_invocations_policy_state_idx
ON model_invocations(policy_id, policy_version, state, created_at);

CREATE INDEX model_invocations_provider_model_idx
ON model_invocations(provider, model, created_at);
