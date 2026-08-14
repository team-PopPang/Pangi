import { FormEvent, useEffect, useState } from "react";
import { Route, Routes } from "react-router-dom";

const plannedSections = ["연동", "도구", "모델 정책", "메모리", "스케줄", "스킬"];

function EmptyOverview() {
  return (
    <main className="content">
      <header className="page-header">
        <div>
          <p className="eyebrow">Pangi 1.0</p>
          <h1>관리자 대시보드</h1>
          <p className="description">
            로컬 Runtime과 최초 관리자 생성 경로가 준비됐다.
          </p>
        </div>
        <span className="status-badge">
          <span className="status-dot" aria-hidden="true" /> Runtime ready
        </span>
      </header>

      <section className="empty-card" aria-labelledby="empty-title">
        <div className="empty-mark" aria-hidden="true">P</div>
        <div>
          <h2 id="empty-title">Admin Shell이 연결됐다</h2>
          <p>이 화면은 설치된 Python Wheel에서 같은 Origin으로 제공된다.</p>
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
    <main className="content bootstrap-content">
      <header className="page-header">
        <div>
          <p className="eyebrow">First-run setup</p>
          <h1>최초 관리자 만들기</h1>
          <p className="description">일회성 Bootstrap Grant로 로컬 관리자 계정을 만든다.</p>
        </div>
      </header>
      <form className="bootstrap-card" onSubmit={submit}>
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
            관리자가 생성됐다. 로그인 기능은 다음 단계에서 연결된다.
          </p>
        ) : null}
        {state === "error" ? (
          <p className="form-message error" role="alert">
            관리자 생성에 실패했다. URL이 만료됐거나 이미 사용됐을 수 있다.
          </p>
        ) : null}
      </form>
    </main>
  );
}

export function App() {
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
        <p className="sidebar-note">Organization-operated agent runtime</p>
      </aside>
      <Routes>
        <Route path="/bootstrap" element={<BootstrapAdmin />} />
        <Route path="*" element={<EmptyOverview />} />
      </Routes>
    </div>
  );
}
