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
            로컬 Runtime이 준비됐다. 인증과 관리 기능은 다음 구현 단계에서 연결된다.
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
        <Route path="*" element={<EmptyOverview />} />
      </Routes>
    </div>
  );
}
