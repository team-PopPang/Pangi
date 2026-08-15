CREATE TABLE audit_events (
    id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 255),
    actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 255),
    action TEXT NOT NULL CHECK (
        length(action) BETWEEN 3 AND 120
        AND instr(action, '.') > 1
        AND action = lower(action)
    ),
    resource_type TEXT NOT NULL CHECK (
        length(resource_type) BETWEEN 1 AND 120
        AND resource_type = lower(resource_type)
    ),
    resource_id TEXT NOT NULL CHECK (length(resource_id) BETWEEN 1 AND 255),
    metadata_json TEXT NOT NULL CHECK (
        length(metadata_json) BETWEEN 2 AND 65536
        AND json_valid(metadata_json)
        AND json_type(metadata_json) = 'object'
        AND json_extract(metadata_json, '$.schema_version') = 1
        AND json_extract(metadata_json, '$.outcome') IN ('succeeded', 'failed', 'denied')
        AND json_type(metadata_json, '$.details') = 'object'
        AND json_type(metadata_json, '$.policy') = 'object'
        AND json_type(metadata_json, '$.redaction') = 'object'
    ),
    created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL)
);

CREATE INDEX audit_events_created_idx
ON audit_events(created_at DESC, id DESC);

CREATE INDEX audit_events_actor_created_idx
ON audit_events(actor_id, created_at DESC, id DESC);

CREATE INDEX audit_events_action_created_idx
ON audit_events(action, created_at DESC, id DESC);

CREATE INDEX audit_events_resource_created_idx
ON audit_events(resource_type, resource_id, created_at DESC, id DESC);

CREATE INDEX audit_events_outcome_created_idx
ON audit_events(json_extract(metadata_json, '$.outcome'), created_at DESC, id DESC);

CREATE TRIGGER audit_events_reject_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit events are immutable');
END;

CREATE TRIGGER audit_events_reject_unexpired_delete
BEFORE DELETE ON audit_events
WHEN julianday(OLD.created_at) > julianday('now', '-365 days')
BEGIN
    SELECT RAISE(ABORT, 'audit event retention is active');
END;
