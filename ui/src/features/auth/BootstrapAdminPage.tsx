import { type FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { adminApi } from "../../api/client";
import { PublicShell } from "../../components/layout/PublicShell";

export function BootstrapAdminPage() {
  const [token, setToken] = useState(() => window.location.hash.slice(1));
  const [localId, setLocalId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "success" | "error">("idle");

  useEffect(() => {
    if (window.location.hash.length > 1) {
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
    }
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setState("saving");
    try {
      await adminApi.createBootstrapAdmin({
        token,
        local_id: localId,
        display_name: displayName,
        password,
      });
      setToken("");
      setPassword("");
      setState("success");
    } catch {
      setPassword("");
      setState("error");
    }
  }

  return (
    <PublicShell>
      <main className="auth-content">
        <header className="auth-header">
          <p className="eyebrow">First-run setup</p>
          <h1>최초 관리자 만들기</h1>
          <p className="description">일회성 Bootstrap Grant로 로컬 관리자 계정을 만든다.</p>
        </header>
        <form className="auth-card" onSubmit={submit}>
          {token.length === 0 && state !== "success" ? (
            <p className="form-message error" role="alert">
              유효한 Bootstrap URL이 필요하다. CLI에서 새 URL을 발급해 다시 접속해 주세요.
            </p>
          ) : null}
          <label>
            로컬 아이디
            <input
              autoComplete="username"
              minLength={3}
              maxLength={80}
              onChange={(event) => setLocalId(event.target.value)}
              required
              value={localId}
            />
          </label>
          <label>
            표시 이름
            <input
              autoComplete="name"
              maxLength={80}
              onChange={(event) => setDisplayName(event.target.value)}
              required
              value={displayName}
            />
          </label>
          <label>
            비밀번호
            <input
              autoComplete="new-password"
              minLength={12}
              maxLength={256}
              onChange={(event) => setPassword(event.target.value)}
              required
              type="password"
              value={password}
            />
            <span className="field-hint">12자 이상 입력해 주세요.</span>
          </label>
          <button disabled={token.length === 0 || state === "saving" || state === "success"} type="submit">
            {state === "saving" ? "만드는 중…" : "관리자 만들기"}
          </button>
          {state === "success" ? (
            <p className="form-message success" role="status">
              관리자가 생성됐다. <Link to="/login">로그인 화면으로 이동하기</Link>
            </p>
          ) : null}
          {state === "error" ? (
            <p className="form-message error" role="alert">
              관리자 생성에 실패했다. URL이 만료됐거나 이미 사용됐을 수 있다.
            </p>
          ) : null}
        </form>
      </main>
    </PublicShell>
  );
}
