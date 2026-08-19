import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  adminApi,
  ApiError,
  type ModelPolicyListItem,
} from "../../api/client";
import "./model-policies.css";

const PAGE_SIZE = 20;

const stateLabels = {
  active: "활성",
  draft: "초안",
  retired: "폐기",
} as const;

const retentionLabels = {
  provider_default: "Provider 기본값",
  zero_retention: "Zero retention",
} as const;

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function shortFingerprint(value: string): string {
  return value.length > 18 ? `${value.slice(0, 10)}…${value.slice(-6)}` : value;
}

function BooleanValue({ enabled, enabledLabel, disabledLabel }: {
  enabled: boolean;
  enabledLabel: string;
  disabledLabel: string;
}) {
  return (
    <span className={`policy-value-state ${enabled ? "positive" : "neutral"}`}>
      {enabled ? enabledLabel : disabledLabel}
    </span>
  );
}

function TagList({ values, emptyLabel = "없음" }: {
  values: readonly string[];
  emptyLabel?: string;
}) {
  if (values.length === 0) {
    return <span className="policy-empty-value">{emptyLabel}</span>;
  }
  return (
    <ul className="policy-tags">
      {values.map((value) => <li key={value}>{value}</li>)}
    </ul>
  );
}

function PolicyScope({ item }: { item: ModelPolicyListItem }) {
  const policy = item.policy.egress_policy;
  const titleId = `${item.policy.policy_id}-${item.policy.version}-scope`;
  return (
    <section className="policy-section" aria-labelledby={titleId}>
      <div className="policy-section-heading">
        <div>
          <p className="policy-section-kicker">EGRESS POLICY</p>
          <h3 id={titleId}>허용 범위</h3>
        </div>
        <code title={item.policy.fingerprint}>
          {shortFingerprint(item.policy.fingerprint)}
        </code>
      </div>
      <dl className="policy-definition-grid">
        <div>
          <dt>Provider</dt>
          <dd><TagList values={policy.allowed_providers} /></dd>
        </div>
        <div>
          <dt>Model</dt>
          <dd><TagList values={policy.allowed_models} /></dd>
        </div>
        <div>
          <dt>Region</dt>
          <dd><TagList values={policy.allowed_regions} emptyLabel="Region 없음만 허용" /></dd>
        </div>
        <div>
          <dt>Purpose</dt>
          <dd><TagList values={policy.allowed_purposes} /></dd>
        </div>
        <div>
          <dt>Data Class</dt>
          <dd><TagList values={policy.allowed_data_classes} /></dd>
        </div>
        <div>
          <dt>Source Kind</dt>
          <dd><TagList values={policy.allowed_source_kinds} /></dd>
        </div>
      </dl>
      <dl className="policy-guardrails">
        <div>
          <dt>중앙 Redaction</dt>
          <dd>
            <BooleanValue
              enabled={policy.require_redaction}
              enabledLabel="필수"
              disabledLabel="추가 요구 없음"
            />
          </dd>
        </div>
        <div>
          <dt>Zero retention</dt>
          <dd>
            <BooleanValue
              enabled={policy.require_zero_retention}
              enabledLabel="필수"
              disabledLabel="선택"
            />
          </dd>
        </div>
        <div>
          <dt>Raw content</dt>
          <dd>
            <BooleanValue
              enabled={policy.allow_raw_content}
              enabledLabel="허용"
              disabledLabel="차단"
            />
          </dd>
        </div>
      </dl>
    </section>
  );
}

function PhysicalProfiles({ item }: { item: ModelPolicyListItem }) {
  const titleId = `${item.policy.policy_id}-${item.policy.version}-profiles`;
  return (
    <section className="policy-section" aria-labelledby={titleId}>
      <div className="policy-section-heading">
        <div>
          <p className="policy-section-kicker">ROUTING CANDIDATES</p>
          <h3 id={titleId}>물리 모델 Profile</h3>
        </div>
        <span className="policy-section-count">{item.policy.profiles.length}개</span>
      </div>
      <div className="profile-grid">
        {item.policy.profiles.map((profile) => (
          <article className="profile-card" key={`${profile.profile_id}:${profile.profile_version}`}>
            <header>
              <div>
                <strong>{profile.provider}</strong>
                <span>{profile.model}</span>
              </div>
              <span className={`profile-state ${profile.active ? "active" : "inactive"}`}>
                {profile.active ? "사용" : "비활성"}
              </span>
            </header>
            <dl className="profile-metadata">
              <div><dt>Region</dt><dd>{profile.region ?? "없음"}</dd></div>
              <div><dt>우선순위</dt><dd>{profile.routing_priority}</dd></div>
              <div><dt>Retention</dt><dd>{retentionLabels[profile.retention]}</dd></div>
              <div><dt>Raw content</dt><dd>{profile.allow_raw_content ? "허용" : "차단"}</dd></div>
            </dl>
            <div className="profile-support">
              <div><span>Data Class</span><TagList values={profile.supported_data_classes} /></div>
              <div><span>Source Kind</span><TagList values={profile.supported_source_kinds} /></div>
              <div><span>Purpose</span><TagList values={profile.supported_purposes} /></div>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function InvocationSummary({ item }: { item: ModelPolicyListItem }) {
  const summary = item.invocation_summary;
  const titleId = `${item.policy.policy_id}-${item.policy.version}-invocations`;
  return (
    <section className="policy-section" aria-labelledby={titleId}>
      <div className="policy-section-heading">
        <div>
          <p className="policy-section-kicker">RECENT INVOCATIONS</p>
          <h3 id={titleId}>최근 허용·거부 Summary</h3>
        </div>
        <span className="policy-window">
          {formatDateTime(summary.window_started_at)}부터 7일
        </span>
      </div>
      <div className="invocation-metrics">
        <div className="allowed"><span>허용</span><strong>{summary.allowed_count}</strong></div>
        <div className="denied"><span>거부</span><strong>{summary.denied_count}</strong></div>
      </div>
      <div className="invocation-breakdown">
        <div>
          <h4>Purpose별 호출</h4>
          {summary.purposes.length === 0 ? (
            <p className="policy-empty-value">기록된 호출이 없다.</p>
          ) : (
            <ul className="count-list">
              {summary.purposes.map((purpose) => (
                <li key={purpose.purpose}><span>{purpose.purpose}</span><strong>{purpose.count}</strong></li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <h4>거부 사유</h4>
          {summary.denial_reasons.length === 0 ? (
            <p className="policy-empty-value">거부 기록이 없다.</p>
          ) : (
            <ul className="count-list">
              {summary.denial_reasons.map((reason) => (
                <li key={reason.reason}><span>{reason.reason}</span><strong>{reason.count}</strong></li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}

function PolicyImpact({ item }: { item: ModelPolicyListItem }) {
  const impact = item.impact;
  if (impact === null) {
    return null;
  }
  const titleId = `${item.policy.policy_id}-${item.policy.version}-impact`;
  return (
    <section className="policy-section policy-impact" aria-labelledby={titleId}>
      <div className="policy-section-heading">
        <div>
          <p className="policy-section-kicker">CANDIDATE IMPACT</p>
          <h3 id={titleId}>활성화 전 변경 영향</h3>
        </div>
        <code title={impact.impact_fingerprint}>{shortFingerprint(impact.impact_fingerprint)}</code>
      </div>
      <dl className="impact-grid">
        <div><dt>변경</dt><dd><TagList values={impact.changed_policy_keys} /></dd></div>
        <div><dt>추가</dt><dd><TagList values={impact.added_policy_keys} /></dd></div>
        <div><dt>제거</dt><dd><TagList values={impact.removed_policy_keys} /></dd></div>
      </dl>
      <div className="integration-unavailable" role="note">
        <strong>사용처와 필수 Eval Suite는 아직 확인할 수 없다.</strong>
        <p>Consumer Registry와 Eval 실행기는 후속 WBS에서 연결한다. 빈 목록을 사용처 없음으로 해석하지 않는다.</p>
      </div>
    </section>
  );
}

function PolicyCard({ item }: { item: ModelPolicyListItem }) {
  const { policy } = item;
  const titleId = `policy-${policy.policy_id}-${policy.version}`;
  return (
    <article className="policy-card" aria-labelledby={titleId}>
      <header className="policy-card-header">
        <div>
          <div className="policy-title-row">
            <h2 id={titleId}>{policy.profile}</h2>
            <span className={`policy-state ${policy.state}`}>{stateLabels[policy.state]}</span>
          </div>
          <p>Policy {policy.policy_id} · Version {policy.version}</p>
        </div>
        <dl className="policy-timestamps">
          <div><dt>생성</dt><dd>{formatDateTime(policy.created_at)}</dd></div>
          <div><dt>수정</dt><dd>{formatDateTime(policy.updated_at)}</dd></div>
        </dl>
      </header>
      <div className="policy-card-body">
        <PolicyScope item={item} />
        <PhysicalProfiles item={item} />
        <InvocationSummary item={item} />
        <PolicyImpact item={item} />
      </div>
    </article>
  );
}

function errorMessage(error: ApiError): string {
  if (error.status === 403) {
    return "모델 정책을 조회할 관리자 권한이 없다.";
  }
  if (error.status === 0) {
    return "서버에 연결하지 못했다. Pangi 실행 상태를 확인하고 다시 시도해 주세요.";
  }
  return "모델 정책을 불러오지 못했다. 잠시 후 다시 시도해 주세요.";
}

export function ModelPoliciesPage() {
  const navigate = useNavigate();
  const requestSequence = useRef(0);
  const [items, setItems] = useState<ModelPolicyListItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [retryRequest, setRetryRequest] = useState<{
    cursor: string | null;
    append: boolean;
  } | null>(null);

  const loadPolicies = useCallback(async (cursor: string | null, append: boolean) => {
    const sequence = ++requestSequence.current;
    if (append) {
      setLoadingMore(true);
    } else {
      setLoading(true);
    }
    setError(null);
    setRetryRequest(null);
    try {
      const payload = await adminApi.listModelPolicies({
        cursor: cursor ?? undefined,
        limit: PAGE_SIZE,
      });
      if (sequence !== requestSequence.current) {
        return;
      }
      setItems((current) => {
        if (!append) {
          return payload.items;
        }
        const known = new Set(current.map((item) => `${item.policy.policy_id}:${item.policy.version}`));
        return [
          ...current,
          ...payload.items.filter((item) => !known.has(`${item.policy.policy_id}:${item.policy.version}`)),
        ];
      });
      setNextCursor(payload.next_cursor);
    } catch (cause) {
      if (sequence !== requestSequence.current) {
        return;
      }
      const apiError = cause instanceof ApiError
        ? cause
        : new ApiError({
          status: 0,
          code: "unknown_error",
          message: "The API request could not be completed",
          requestId: null,
          retryAfterSeconds: null,
          cause,
        });
      if (apiError.status === 401) {
        navigate("/login", { replace: true });
        return;
      }
      setError(apiError);
      setRetryRequest({ cursor, append });
    } finally {
      if (sequence === requestSequence.current) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }, [navigate]);

  useEffect(() => {
    void loadPolicies(null, false);
    return () => {
      requestSequence.current += 1;
    };
  }, [loadPolicies]);

  const initialError = error !== null && items.length === 0;

  return (
    <>
      <header className="page-header policy-page-header">
        <div>
          <p className="eyebrow">MODEL POLICY</p>
          <h1>모델 정책</h1>
          <p className="description">
            Version별 Egress 범위와 최근 모델 호출 판정을 읽기 전용으로 확인한다.
          </p>
        </div>
        <div className="policy-page-actions">
          <span className="readonly-badge">읽기 전용</span>
          <button
            className="policy-button secondary"
            disabled={loading || loadingMore}
            onClick={() => void loadPolicies(null, false)}
            type="button"
          >
            새로고침
          </button>
        </div>
      </header>

      <aside className="policy-page-notice" aria-labelledby="policy-integration-title">
        <div aria-hidden="true">i</div>
        <div>
          <strong id="policy-integration-title">사용처·Eval 연동 전 단계다.</strong>
          <p>현재 화면은 저장된 정책과 안전한 호출 Summary만 제공한다. 평가와 활성화 기능은 WBS-15 연결 후 제공한다.</p>
        </div>
      </aside>

      {loading && items.length === 0 ? (
        <section className="policy-state-card" role="status">
          <span className="policy-spinner" aria-hidden="true" />
          <div><strong>모델 정책을 불러오는 중이다.</strong><p>저장된 Version과 최근 호출 Summary를 확인하고 있다.</p></div>
        </section>
      ) : null}

      {initialError ? (
        <section className="policy-state-card error" role="alert">
          <div><strong>조회에 실패했다.</strong><p>{errorMessage(error)}</p></div>
          <button className="policy-button secondary" onClick={() => void loadPolicies(null, false)} type="button">
            다시 시도
          </button>
        </section>
      ) : null}

      {!loading && error === null && items.length === 0 ? (
        <section className="policy-state-card empty" aria-labelledby="empty-policy-title">
          <div className="empty-mark" aria-hidden="true">P</div>
          <div>
            <strong id="empty-policy-title">저장된 Model Policy가 없다.</strong>
            <p>Policy가 등록되면 Version, Egress 범위와 최근 호출 Summary가 여기에 표시된다.</p>
          </div>
        </section>
      ) : null}

      {items.length > 0 ? (
        <section className="policy-list" aria-label="Model Policy Version 목록">
          {items.map((item) => (
            <PolicyCard item={item} key={`${item.policy.policy_id}:${item.policy.version}`} />
          ))}
        </section>
      ) : null}

      {error !== null && items.length > 0 ? (
        <div className="policy-pagination-error" role="alert">
          <span>{errorMessage(error)}</span>
          <button
            className="policy-button text"
            onClick={() => {
              if (retryRequest !== null) {
                void loadPolicies(retryRequest.cursor, retryRequest.append);
              }
            }}
            type="button"
          >
            다시 시도
          </button>
        </div>
      ) : null}

      {nextCursor !== null && error === null ? (
        <div className="policy-pagination">
          <button
            className="policy-button secondary"
            disabled={loadingMore}
            onClick={() => void loadPolicies(nextCursor, true)}
            type="button"
          >
            {loadingMore ? "불러오는 중…" : "다음 정책 불러오기"}
          </button>
        </div>
      ) : null}
    </>
  );
}
