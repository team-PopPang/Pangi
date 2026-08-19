ALTER TABLE model_invocations
ADD COLUMN requested_profile TEXT CHECK (
    requested_profile IS NULL OR length(requested_profile) BETWEEN 1 AND 120
);

UPDATE model_invocations
SET requested_profile = (
    SELECT model_policies.name
    FROM model_policies
    WHERE model_policies.id = model_invocations.policy_id
      AND model_policies.version = model_invocations.policy_version
)
WHERE requested_profile IS NULL
  AND policy_id IS NOT NULL
  AND policy_version IS NOT NULL;

CREATE TRIGGER model_invocations_requested_profile_required
BEFORE INSERT ON model_invocations
WHEN NEW.requested_profile IS NULL
BEGIN
    SELECT RAISE(ABORT, 'Model Invocation requested Profile is required');
END;

CREATE INDEX model_invocations_profile_created_idx
ON model_invocations(requested_profile, created_at, id);
