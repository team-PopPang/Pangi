CREATE TABLE run_execution_plans (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    mode TEXT NOT NULL CHECK (mode IN ('direct', 'delegate')),
    schema_version TEXT NOT NULL CHECK (
        schema_version = 'orchestration-execution-v1'
    ),
    plan_json TEXT NOT NULL CHECK (
        json_valid(plan_json)
        AND json_type(plan_json) = 'object'
        AND length(plan_json) BETWEEN 2 AND 500000
    ),
    plan_fingerprint TEXT NOT NULL CHECK (
        length(plan_fingerprint) = 64
        AND plan_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    created_at TEXT NOT NULL
);

CREATE TRIGGER run_execution_plans_immutable_update
BEFORE UPDATE ON run_execution_plans
BEGIN
    SELECT RAISE(ABORT, 'run_execution_plans are immutable');
END;

ALTER TABLE run_steps ADD COLUMN task_json TEXT CHECK (
    task_json IS NULL
    OR (
        json_valid(task_json)
        AND json_type(task_json) = 'object'
        AND length(task_json) BETWEEN 2 AND 200000
    )
);

ALTER TABLE run_steps ADD COLUMN result_json TEXT CHECK (
    result_json IS NULL
    OR (
        json_valid(result_json)
        AND json_type(result_json) = 'object'
        AND length(result_json) BETWEEN 2 AND 1000000
    )
);

ALTER TABLE run_steps ADD COLUMN result_fingerprint TEXT CHECK (
    result_fingerprint IS NULL
    OR (
        length(result_fingerprint) = 64
        AND result_fingerprint NOT GLOB '*[^0-9a-f]*'
    )
);

CREATE TRIGGER run_steps_task_definition_immutable
BEFORE UPDATE OF task_json ON run_steps
WHEN OLD.task_json IS NOT NEW.task_json
BEGIN
    SELECT RAISE(ABORT, 'run_steps task definitions are immutable');
END;

CREATE TRIGGER run_steps_result_immutable
BEFORE UPDATE OF result_json, result_fingerprint ON run_steps
WHEN OLD.result_json IS NOT NULL OR OLD.result_fingerprint IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'run_steps results are immutable');
END;

CREATE TRIGGER run_steps_result_pair_insert
BEFORE INSERT ON run_steps
WHEN (NEW.result_json IS NULL) != (NEW.result_fingerprint IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'run_steps result fields must be paired');
END;

CREATE TRIGGER run_steps_result_pair_update
BEFORE UPDATE OF result_json, result_fingerprint ON run_steps
WHEN (NEW.result_json IS NULL) != (NEW.result_fingerprint IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'run_steps result fields must be paired');
END;
