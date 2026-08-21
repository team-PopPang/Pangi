export interface paths {
    "/api/v1/audit-events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Audit Events */
        get: operations["listAuditEvents"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/auth/login": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Login */
        post: operations["login"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/auth/logout": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Logout */
        post: operations["logout"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/auth/session": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Session */
        get: operations["getAuthSession"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/auth/session/rotate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Rotate Session */
        post: operations["rotateAuthSession"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/bootstrap/admin": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Bootstrap Admin */
        post: operations["createBootstrapAdmin"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/model-policies": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Model Policies */
        get: operations["listModelPolicies"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/model-policies/{policy_id}/versions/{version}/activate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Activate Model Policy */
        post: operations["activateModelPolicy"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/model-policies/{policy_id}/versions/{version}/evaluate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Evaluate Model Policy */
        post: operations["evaluateModelPolicy"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Runs */
        get: operations["listRuns"];
        put?: never;
        /** Create Run */
        post: operations["createRun"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/runs/{run_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Run */
        get: operations["getRun"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/runs/{run_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Cancel Run */
        post: operations["cancelRun"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/runs/{run_id}/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Run Events */
        get: operations["getRunEvents"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/runs/metrics": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Run Queue Metrics */
        get: operations["getRunQueueMetrics"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** AdminResponse */
        AdminResponse: {
            /** Display Name */
            display_name: string;
            /** Local Id */
            local_id: string;
            /**
             * Role
             * @constant
             */
            role: "admin";
            /**
             * Status
             * @constant
             */
            status: "active";
            /** User Id */
            user_id: string;
        };
        /** AttachmentResponse */
        AttachmentResponse: {
            /** Display Name */
            display_name: string | null;
            /** Fingerprint */
            fingerprint: string | null;
            /** Media Type */
            media_type: string | null;
            /** Reference */
            reference: string;
            /** Size Bytes */
            size_bytes: number | null;
        };
        /** AuditEventListEnvelope */
        AuditEventListEnvelope: {
            /** Items */
            items: components["schemas"]["AuditEventResponse"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** AuditEventResponse */
        AuditEventResponse: {
            /** Action */
            action: string;
            /** Actor Id */
            actor_id: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Id */
            id: string;
            /** Metadata */
            metadata: {
                [key: string]: unknown;
            };
            outcome: components["schemas"]["AuditOutcome"];
            /** Resource Id */
            resource_id: string;
            /** Resource Type */
            resource_type: string;
        };
        /**
         * AuditOutcome
         * @description Stable outcomes shared by successful and rejected management actions.
         * @enum {string}
         */
        AuditOutcome: "succeeded" | "failed" | "denied";
        /**
         * BootstrapAdminRequest
         * @description One-time credentials used to create the first local administrator.
         */
        BootstrapAdminRequest: {
            /** Display Name */
            display_name: string;
            /** Local Id */
            local_id: string;
            /** Password */
            password: string;
            /** Token */
            token: string;
        };
        /** BootstrapAdminResponse */
        BootstrapAdminResponse: {
            admin: components["schemas"]["AdminResponse"];
        };
        /**
         * DataClass
         * @description Sensitivity classes ordered from least to most restrictive.
         * @enum {string}
         */
        DataClass: "public" | "internal" | "confidential" | "personal" | "restricted";
        /** ErrorBody */
        ErrorBody: {
            /** Code */
            code: string;
            /** Message */
            message: string;
            /** Request Id */
            request_id: string;
        };
        /** ErrorEnvelope */
        ErrorEnvelope: {
            error: components["schemas"]["ErrorBody"];
        };
        /**
         * EventVisibility
         * @enum {string}
         */
        EventVisibility: "public" | "admin" | "internal";
        /**
         * LoginRequest
         * @description Local administrator credentials.
         */
        LoginRequest: {
            /** Local Id */
            local_id: string;
            /** Password */
            password: string;
        };
        /** ModelEgressPolicyResponse */
        ModelEgressPolicyResponse: {
            /** Allow Raw Content */
            allow_raw_content: boolean;
            /** Allowed Data Classes */
            allowed_data_classes: components["schemas"]["DataClass"][];
            /** Allowed Models */
            allowed_models: string[];
            /** Allowed Providers */
            allowed_providers: string[];
            /** Allowed Purposes */
            allowed_purposes: components["schemas"]["ModelPurpose"][];
            /** Allowed Regions */
            allowed_regions: string[];
            /** Allowed Source Kinds */
            allowed_source_kinds: string[];
            /** Policy Id */
            policy_id: string;
            /** Policy Version */
            policy_version: string;
            /** Profile */
            profile: string;
            /** Require Redaction */
            require_redaction: boolean;
            /** Require Zero Retention */
            require_zero_retention: boolean;
        };
        /** ModelInvocationPurposeCountResponse */
        ModelInvocationPurposeCountResponse: {
            /** Count */
            count: number;
            /** Purpose */
            purpose: string;
        };
        /** ModelInvocationReasonCountResponse */
        ModelInvocationReasonCountResponse: {
            /** Count */
            count: number;
            /** Reason */
            reason: string;
        };
        /** ModelInvocationSummaryResponse */
        ModelInvocationSummaryResponse: {
            /** Allowed Count */
            allowed_count: number;
            /** Denial Reasons */
            denial_reasons: components["schemas"]["ModelInvocationReasonCountResponse"][];
            /** Denied Count */
            denied_count: number;
            /** Purposes */
            purposes: components["schemas"]["ModelInvocationPurposeCountResponse"][];
            /**
             * Window Ended At
             * Format: date-time
             */
            window_ended_at: string;
            /**
             * Window Started At
             * Format: date-time
             */
            window_started_at: string;
        };
        /** ModelPolicyActivateRequest */
        ModelPolicyActivateRequest: {
            /** Candidate Fingerprint */
            candidate_fingerprint: string;
            /** Eval Run Id */
            eval_run_id: string;
            /** Impact Fingerprint */
            impact_fingerprint: string;
        };
        /** ModelPolicyActivationEnvelope */
        ModelPolicyActivationEnvelope: {
            /** Impact Fingerprint */
            impact_fingerprint: string;
            policy: components["schemas"]["ModelPolicyVersionResponse"];
            /** Replayed */
            replayed: boolean;
        };
        /** ModelPolicyEvaluateRequest */
        ModelPolicyEvaluateRequest: {
            /** Candidate Fingerprint */
            candidate_fingerprint: string;
        };
        /** ModelPolicyEvaluationEnvelope */
        ModelPolicyEvaluationEnvelope: {
            /** Eval Run Id */
            eval_run_id: string;
            impact: components["schemas"]["ModelPolicyImpactResponse"];
            /**
             * State
             * @enum {string}
             */
            state: "queued" | "running" | "passed" | "failed";
        };
        /** ModelPolicyImpactResponse */
        ModelPolicyImpactResponse: {
            /** Added Policy Keys */
            added_policy_keys: string[];
            /** Affected Consumers */
            affected_consumers: string[];
            /** Affected Policy Keys */
            affected_policy_keys: string[];
            /** Baseline Snapshot Fingerprint */
            baseline_snapshot_fingerprint: string | null;
            /** Candidate Snapshot Fingerprint */
            candidate_snapshot_fingerprint: string;
            /** Changed Policy Keys */
            changed_policy_keys: string[];
            /**
             * Consumer Resolution
             * @constant
             */
            consumer_resolution: "unavailable";
            /** Impact Fingerprint */
            impact_fingerprint: string;
            /** Removed Policy Keys */
            removed_policy_keys: string[];
            /** Required Eval Suites */
            required_eval_suites: string[];
            /**
             * Schema Version
             * @constant
             */
            schema_version: "model-policy-impact-v1";
        };
        /** ModelPolicyListEnvelope */
        ModelPolicyListEnvelope: {
            /** Items */
            items: components["schemas"]["ModelPolicyListItemResponse"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** ModelPolicyListItemResponse */
        ModelPolicyListItemResponse: {
            impact: components["schemas"]["ModelPolicyImpactResponse"] | null;
            invocation_summary: components["schemas"]["ModelInvocationSummaryResponse"];
            policy: components["schemas"]["ModelPolicyVersionResponse"];
        };
        /**
         * ModelPolicyState
         * @description Lifecycle states for one immutable Model Policy version.
         * @enum {string}
         */
        ModelPolicyState: "draft" | "active" | "retired";
        /** ModelPolicyVersionResponse */
        ModelPolicyVersionResponse: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            egress_policy: components["schemas"]["ModelEgressPolicyResponse"];
            /** Eval Run Id */
            eval_run_id: string | null;
            /** Fingerprint */
            fingerprint: string;
            /** Policy Id */
            policy_id: string;
            /** Profile */
            profile: string;
            /** Profiles */
            profiles: components["schemas"]["ModelProfileResponse"][];
            state: components["schemas"]["ModelPolicyState"];
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Version */
            version: string;
        };
        /** ModelProfileResponse */
        ModelProfileResponse: {
            /** Active */
            active: boolean;
            /** Allow Raw Content */
            allow_raw_content: boolean;
            /** Fingerprint */
            fingerprint: string;
            /** Model */
            model: string;
            /** Profile */
            profile: string;
            /** Profile Id */
            profile_id: string;
            /** Profile Version */
            profile_version: string;
            /** Provider */
            provider: string;
            /** Region */
            region: string | null;
            retention: components["schemas"]["ModelRetention"];
            /** Routing Priority */
            routing_priority: number;
            /** Supported Data Classes */
            supported_data_classes: components["schemas"]["DataClass"][];
            /** Supported Purposes */
            supported_purposes: components["schemas"]["ModelPurpose"][];
            /** Supported Source Kinds */
            supported_source_kinds: string[];
        };
        /**
         * ModelPurpose
         * @enum {string}
         */
        ModelPurpose: "orchestration" | "subagent" | "skill" | "eval" | "red_team";
        /**
         * ModelRetention
         * @enum {string}
         */
        ModelRetention: "provider_default" | "zero_retention";
        /**
         * PrincipalChannel
         * @enum {string}
         */
        PrincipalChannel: "slack" | "api" | "dashboard" | "scheduler" | "eval";
        /** PrincipalResponse */
        PrincipalResponse: {
            /** Display Name */
            display_name: string;
            role: components["schemas"]["UserRole"];
            status: components["schemas"]["UserStatus"];
            /** User Id */
            user_id: string;
        };
        /** RunCancellationEnvelope */
        RunCancellationEnvelope: {
            /** Changed */
            changed: boolean;
            run: components["schemas"]["RunResponse"];
        };
        /**
         * RunCreateRequest
         * @description Untrusted text-only fields accepted by the local Run endpoint.
         */
        RunCreateRequest: {
            /** Explicit Skill */
            explicit_skill?: string | null;
            /** Text */
            text: string;
            /** Thread Key */
            thread_key?: string | null;
        };
        /** RunEnvelope */
        RunEnvelope: {
            run: components["schemas"]["RunResponse"];
        };
        /** RunEventListEnvelope */
        RunEventListEnvelope: {
            /** Items */
            items: components["schemas"]["RunEventResponse"][];
            /** Next After Index */
            next_after_index: number | null;
            /** Terminal */
            terminal: boolean;
        };
        /** RunEventResponse */
        RunEventResponse: {
            /** Attributes */
            attributes: {
                [key: string]: unknown;
            };
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Index */
            index: number;
            /** Message */
            message: string | null;
            /** Run Id */
            run_id: string;
            /** Step Id */
            step_id: string | null;
            /** Type */
            type: string;
            visibility: components["schemas"]["EventVisibility"];
        };
        /** RunListEnvelope */
        RunListEnvelope: {
            /** Items */
            items: components["schemas"]["RunSummaryResponse"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /**
         * RunMode
         * @enum {string}
         */
        RunMode: "direct" | "delegate" | "skill";
        /** RunQueueMetricsResponse */
        RunQueueMetricsResponse: {
            /** Expired Lease Count */
            expired_lease_count: number;
            /** Oldest Queued Age Seconds */
            oldest_queued_age_seconds: number | null;
            /** Oldest Queued At */
            oldest_queued_at: string | null;
            /** Queue Depth */
            queue_depth: number;
            /** Running Count */
            running_count: number;
        };
        /** RunRequestResponse */
        RunRequestResponse: {
            /** Attachments */
            attachments: components["schemas"]["AttachmentResponse"][];
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Explicit Skill */
            explicit_skill: string | null;
            /** Principal Id */
            principal_id: string;
            /** Request Id */
            request_id: string;
            /** Schedule Id */
            schedule_id: string | null;
            /** Text */
            text: string;
            /** Thread Key */
            thread_key: string | null;
            trigger: components["schemas"]["PrincipalChannel"];
        };
        /** RunResponse */
        RunResponse: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Error Code */
            error_code: string | null;
            /** Finished At */
            finished_at: string | null;
            /** Id */
            id: string;
            mode: components["schemas"]["RunMode"] | null;
            request: components["schemas"]["RunRequestResponse"];
            /** Revision */
            revision: number;
            /** Skill Version Id */
            skill_version_id: string | null;
            /** Started At */
            started_at: string | null;
            state: components["schemas"]["RunState"];
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Warnings */
            warnings: string[];
        };
        /**
         * RunState
         * @enum {string}
         */
        RunState: "received" | "blocked" | "planning" | "queued" | "running" | "composing" | "completed" | "failed" | "cancelled" | "interrupted";
        /** RunSubmissionEnvelope */
        RunSubmissionEnvelope: {
            /** Replayed */
            replayed: boolean;
            run: components["schemas"]["RunResponse"];
        };
        /** RunSummaryResponse */
        RunSummaryResponse: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Error Code */
            error_code: string | null;
            /** Finished At */
            finished_at: string | null;
            /** Id */
            id: string;
            mode: components["schemas"]["RunMode"] | null;
            /** Principal Id */
            principal_id: string;
            /** Request Id */
            request_id: string;
            /** Revision */
            revision: number;
            /** Skill Version Id */
            skill_version_id: string | null;
            /** Started At */
            started_at: string | null;
            state: components["schemas"]["RunState"];
            trigger: components["schemas"]["PrincipalChannel"];
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Warning Count */
            warning_count: number;
        };
        /** SessionEnvelope */
        SessionEnvelope: {
            session: components["schemas"]["SessionResponse"];
        };
        /** SessionResponse */
        SessionResponse: {
            /**
             * Expires At
             * Format: date-time
             */
            expires_at: string;
            principal: components["schemas"]["PrincipalResponse"];
            /** Rotation Due */
            rotation_due: boolean;
            /**
             * Rotation Due At
             * Format: date-time
             */
            rotation_due_at: string;
        };
        /**
         * UserRole
         * @enum {string}
         */
        UserRole: "member" | "skill_author" | "admin" | "system";
        /**
         * UserStatus
         * @enum {string}
         */
        UserStatus: "active" | "disabled";
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    listAuditEvents: {
        parameters: {
            query?: {
                action?: string[] | null;
                actor_id?: string | null;
                cursor?: string | null;
                from?: string | null;
                limit?: number;
                outcome?: components["schemas"]["AuditOutcome"][] | null;
                resource_id?: string | null;
                resource_type?: string | null;
                to?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AuditEventListEnvelope"];
                };
            };
            /** @description Invalid Audit cursor or filter */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authentication required */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Administrator required */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Audit store unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    login: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LoginRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SessionEnvelope"];
                };
            };
            /** @description Secure transport required */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Credentials rejected */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Origin rejected */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Login rate limit exceeded */
            429: {
                headers: {
                    /** @description Seconds before another login attempt */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    logout: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Authentication required */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description CSRF or origin rejected */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    getAuthSession: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SessionEnvelope"];
                };
            };
            /** @description Authentication required */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    rotateAuthSession: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SessionEnvelope"];
                };
            };
            /** @description Authentication required */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description CSRF or origin rejected */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    createBootstrapAdmin: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["BootstrapAdminRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BootstrapAdminResponse"];
                };
            };
            /** @description Bootstrap grant unavailable */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Origin rejected */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Bootstrap state conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    listModelPolicies: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ModelPolicyListEnvelope"];
                };
            };
            /** @description Invalid Policy cursor */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authentication required */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Administrator required */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Policy store unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    activateModelPolicy: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": string;
            };
            path: {
                policy_id: string;
                version: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ModelPolicyActivateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ModelPolicyActivationEnvelope"];
                };
            };
            /** @description Authentication required */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description CSRF or role rejected */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Policy version not found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Eval or Policy conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Policy or Eval unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    evaluateModelPolicy: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": string;
            };
            path: {
                policy_id: string;
                version: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ModelPolicyEvaluateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ModelPolicyEvaluationEnvelope"];
                };
            };
            /** @description Authentication required */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description CSRF or role rejected */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Policy version not found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Policy state conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Eval runtime unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    listRuns: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
                state?: components["schemas"]["RunState"][] | null;
                trigger?: components["schemas"]["PrincipalChannel"][] | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunListEnvelope"];
                };
            };
            /** @description Invalid Run cursor */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authentication required */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Run store unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    createRun: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RunCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunSubmissionEnvelope"];
                };
            };
            /** @description Authentication required */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description CSRF, origin, or policy rejected */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Idempotency conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Request or Guardrail validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Run rate limit exceeded */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Run submission unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    getRun: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunEnvelope"];
                };
            };
            /** @description Authentication required */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Run not found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Run store unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    cancelRun: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunCancellationEnvelope"];
                };
            };
            /** @description Authentication required */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description CSRF or origin rejected */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Run not found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Run state conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Run Queue unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    getRunEvents: {
        parameters: {
            query?: {
                after?: string | null;
                limit?: number;
            };
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description JSON Event page or resumable Event stream */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunEventListEnvelope"];
                    "text/event-stream": string;
                };
            };
            /** @description Invalid Event cursor */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Authentication required */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Run not found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Event store unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    getRunQueueMetrics: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunQueueMetricsResponse"];
                };
            };
            /** @description Authentication required */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Administrator required */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Unexpected server error */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Metric store unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
}
