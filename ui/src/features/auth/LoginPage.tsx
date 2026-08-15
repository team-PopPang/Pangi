import { type FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { adminApi, ApiError } from "../../api/client";
import { PublicShell } from "../../components/layout/PublicShell";

export function LoginPage() {
  const navigate = useNavigate();
  const [localId, setLocalId] = useState("");
  const [password, setPassword] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "error" | "limited">("idle");

  useEffect(() => {
    void adminApi.getSession().then(
      () => navigate("/", { replace: true }),
      () => undefined,
    );
  }, [navigate]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setState("saving");
    try {
      await adminApi.login({ local_id: localId, password });
      setPassword("");
      navigate("/", { replace: true });
    } catch (error) {
      setPassword("");
      setState(error instanceof ApiError && error.status === 429 ? "limited" : "error");
    }
  }

  return (
    <PublicShell>
      <main className="auth-content compact">
        <header className="auth-header">
          <p className="eyebrow">Local Admin</p>
          <h1>관리자 로그인</h1>
          <p className="description">Bootstrap에서 만든 로컬 관리자 계정으로 로그인한다.</p>
        </header>
        <form className="auth-card" onSubmit={submit}>
          <label>
            로컬 아이디
            <input
              autoComplete="username"
              maxLength={80}
              onChange={(event) => setLocalId(event.target.value)}
              required
              value={localId}
            />
          </label>
          <label>
            비밀번호
            <input
              autoComplete="current-password"
              maxLength={256}
              onChange={(event) => setPassword(event.target.value)}
              required
              type="password"
              value={password}
            />
          </label>
          <button disabled={state === "saving"} type="submit">
            {state === "saving" ? "확인하는 중…" : "로그인"}
          </button>
          {state === "error" ? (
            <p className="form-message error" role="alert">
              아이디 또는 비밀번호를 확인해 주세요.
            </p>
          ) : null}
          {state === "limited" ? (
            <p className="form-message error" role="alert">
              로그인 시도가 너무 많다. 잠시 후 다시 시도해 주세요.
            </p>
          ) : null}
        </form>
      </main>
    </PublicShell>
  );
}
