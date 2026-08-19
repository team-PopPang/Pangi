import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";

import { adminApi, ApiError, type SessionInfo } from "../../api/client";
import { AdminLayout } from "../../components/layout/AdminLayout";
import { PublicShell } from "../../components/layout/PublicShell";
import { ModelPoliciesPage } from "../model-policies/ModelPoliciesPage";
import { OverviewPage } from "../overview/OverviewPage";

function ModelPolicyRoute({ session }: { session: SessionInfo }) {
  if (session.principal.role === "admin") {
    return <ModelPoliciesPage />;
  }
  return (
    <>
      <header className="page-header">
        <div>
          <p className="eyebrow">ACCESS CONTROL</p>
          <h1>접근 권한이 없다</h1>
          <p className="description">모델 정책은 관리자만 조회할 수 있다.</p>
        </div>
      </header>
      <section className="empty-card" aria-labelledby="permission-title">
        <div className="empty-mark" aria-hidden="true">!</div>
        <div>
          <h2 id="permission-title">관리자 권한이 필요하다</h2>
          <p>권한이 필요한 경우 Pangi 관리자에게 문의해 주세요.</p>
        </div>
      </section>
    </>
  );
}

export function AuthenticatedApp() {
  const navigate = useNavigate();
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let active = true;
    async function loadSession() {
      try {
        let payload = await adminApi.getSession();
        if (payload.session.rotation_due) {
          try {
            payload = await adminApi.rotateSession();
          } catch (rotationError) {
            if (!(rotationError instanceof ApiError) || rotationError.code === "network_error") {
              throw rotationError;
            }
          }
        }
        if (active) {
          setSession(payload.session);
        }
      } catch (sessionError) {
        if (sessionError instanceof ApiError && sessionError.status === 401) {
          navigate("/login", { replace: true });
          return;
        }
        if (active) {
          setError(true);
        }
      }
    }
    void loadSession();
    return () => {
      active = false;
    };
  }, [navigate]);

  async function logout() {
    try {
      await adminApi.logout();
      navigate("/login", { replace: true });
    } catch {
      setError(true);
    }
  }

  if (error) {
    return (
      <PublicShell>
        <main className="auth-content compact">
          <p className="form-message error" role="alert">
            인증 상태를 확인하지 못했다. 페이지를 새로고침해 주세요.
          </p>
        </main>
      </PublicShell>
    );
  }
  if (session === null) {
    return <p className="loading-state" role="status">인증 상태를 확인하는 중…</p>;
  }

  return (
    <AdminLayout session={session} onLogout={() => void logout()}>
      <Routes>
        <Route path="/" element={<OverviewPage session={session} />} />
        <Route path="/model-policies" element={<ModelPolicyRoute session={session} />} />
        <Route path="*" element={<Navigate replace to="/" />} />
      </Routes>
    </AdminLayout>
  );
}
