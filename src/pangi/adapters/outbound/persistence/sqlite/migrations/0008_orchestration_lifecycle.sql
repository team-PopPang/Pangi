CREATE TABLE run_outputs (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    schema_version TEXT NOT NULL CHECK (
        schema_version = 'orchestration-output-v1'
    ),
    markdown TEXT NOT NULL CHECK (
        length(trim(markdown)) BETWEEN 1 AND 1000000
    ),
    evidence_links_json TEXT NOT NULL CHECK (
        json_valid(evidence_links_json)
        AND json_type(evidence_links_json) = 'array'
        AND length(evidence_links_json) BETWEEN 2 AND 500000
    ),
    content_fingerprint TEXT NOT NULL CHECK (
        length(content_fingerprint) = 64
        AND content_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    guardrail_metadata_json TEXT NOT NULL CHECK (
        json_valid(guardrail_metadata_json)
        AND json_type(guardrail_metadata_json) = 'object'
        AND length(guardrail_metadata_json) BETWEEN 2 AND 200000
    ),
    created_at TEXT NOT NULL
);

CREATE TRIGGER run_outputs_immutable_update
BEFORE UPDATE ON run_outputs
BEGIN
    SELECT RAISE(ABORT, 'run_outputs are immutable');
END;

