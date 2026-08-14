import { FormEvent, ReactNode, useEffect, useState } from "react";
import { Link, Route, Routes, useNavigate } from "react-router-dom";

const plannedSections = ["연동", "도구", "모델 정책", "메모리", "스케줄", "스킬"];

type Principal = {
  user_id: string;
  display_name: string;
  role: "member" | "skill_author" | "admin" | "system";
  status: "active" | "disabled";
};

type SessionInfo = {
  principal: Principal;
  expires_at: string;
  rotation_due_at: string;
  rotation_due: boolean;
};

type SessionResponse = { session: SessionInfo };

function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  for (const item of document.cookie.split(";")) {
    const cookie = item.trim();
    if (cookie.startsWith(prefix)) {
      return decodeURIComponent(cookie.slice(prefix.length));
    }
  }
  return null;
}

function csrfToken(): string | null {
  return readCookie("__Host-pangi_csrf") ?? readCookie("pangi_csrf");
}

function PublicPage({ children }: { children: ReactNode }) {
  return (
    <div className="public-shell">
      <Link className="public-brand" to="/">
        <span className="brand-mark" aria-hidden="true">P</span>
        <span>Pangi</span>
      </Link>
      {children}
    </div>
  );
}

function EmptyOverview({ session }: { session: SessionInfo }) {
  return (
    <main className="content">
      <header className="page-header">
        <div>
          <p className="eyebrow">Pangi 1.0</p>
          <h1>관리자 대시보드</h1>
          <p className="description">
            {session.principal.display_name} 계정으로 안전하게 로그인했다.
          </p>
        </div>
        <span className="status-badge">
          <span className="status-dot" aria-hidden="true" /> Authenticated
        </span>
      </header>

      <section className="empty-card" aria-labelledby="empty-title">
        <div className="empty-mark" aria-hidden="true">P</div>
        <div>
          <h2 id="empty-title">Local Session이 연결됐다</h2>
          <p>인증된 Admin Shell과 CSRF로 보호된 변경 요청을 사용할 수 있다.</p>
        </div>
      </section>
    </main>
  );
}

function BootstrapAdmin() {
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
      const response = await fetch("/api/v1/bootstrap/admin", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, local_id: localId, display_name: displayName, password }),
      });
      if (!response.ok) {
        throw new Error("bootstrap failed");
      }
      setToken("");
      setPassword("");
      setState("success");
    } catch {
      setPassword("");
      setState("error");
    }
  }

  return (
    <PublicPage>
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
    </PublicPage>
  );
}

function Login() {
  const navigate = useNavigate();
  const [localId, setLocalId] = useState("");
  const [password, setPassword] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "error" | "limited">("idle");

  useEffect(() => {
    void fetch("/api/v1/auth/session").then((response) => {
      if (response.ok) {
        navigate("/", { replace: true });
      }
    });
  }, [navigate]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setState("saving");
    try {
      const response = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ local_id: localId, password }),
      });
      setPassword("");
      if (response.status === 429) {
        setState("limited");
        return;
      }
      if (!response.ok) {
        setState("error");
        return;
      }
      navigate("/", { replace: true });
    } catch {
      setPassword("");
      setState("error");
    }
  }

  return (
    <PublicPage>
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
    </PublicPage>
  );
}

function AuthenticatedShell() {
  const navigate = useNavigate();
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let active = true;
    async function loadSession() {
      try {
        const response = await fetch("/api/v1/auth/session");
        if (response.status === 401) {
          navigate("/login", { replace: true });
          return;
        }
        if (!response.ok) {
          throw new Error("session failed");
        }
        let payload = await response.json() as SessionResponse;
        if (payload.session.rotation_due) {
          const csrf = csrfToken();
          if (csrf !== null) {
            const rotated = await fetch("/api/v1/auth/session/rotate", {
              method: "POST",
              headers: { "X-CSRF-Token": csrf },
            });
            if (rotated.ok) {
              payload = await rotated.json() as SessionResponse;
            }
          }
        }
        if (active) {
          setSession(payload.session);
        }
      } catch {
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
    const csrf = csrfToken();
    if (csrf === null) {
      setError(true);
      return;
    }
    try {
      const response = await fetch("/api/v1/auth/logout", {
        method: "POST",
        headers: { "X-CSRF-Token": csrf },
      });
      if (!response.ok) {
        throw new Error("logout failed");
      }
      navigate("/login", { replace: true });
    } catch {
      setError(true);
    }
  }

  if (error) {
    return (
      <PublicPage>
        <main className="auth-content compact">
          <p className="form-message error" role="alert">
            인증 상태를 확인하지 못했다. 페이지를 새로고침해 주세요.
          </p>
        </main>
      </PublicPage>
    );
  }
  if (session === null) {
    return <p className="loading-state" role="status">인증 상태를 확인하는 중…</p>;
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">P</span>
          <span>Pangi</span>
        </div>
        <nav aria-label="관리자 메뉴">
          <a className="nav-item selected" href="/">개요</a>
          {plannedSections.map((section) => (
            <span className="nav-item disabled" key={section}>{section}</span>
          ))}
        </nav>
        <div className="session-summary">
          <strong>{session.principal.display_name}</strong>
          <span>{session.principal.role}</span>
          <button className="logout-button" onClick={() => void logout()} type="button">로그아웃</button>
        </div>
        <p className="sidebar-note">Organization-operated agent runtime</p>
      </aside>
      <EmptyOverview session={session} />
    </div>
  );
}

export function App() {
  return (
    <Routes>
      <Route path="/bootstrap" element={<BootstrapAdmin />} />
      <Route path="/login" element={<Login />} />
      <Route path="*" element={<AuthenticatedShell />} />
    </Routes>
  );
}
