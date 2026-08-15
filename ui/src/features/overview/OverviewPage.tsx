import type { SessionInfo } from "../../api/client";

export function OverviewPage({ session }: { session: SessionInfo }) {
  return (
    <>
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
    </>
  );
}
