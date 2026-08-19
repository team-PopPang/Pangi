import {
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { Link, NavLink } from "react-router-dom";

import type { SessionInfo } from "../../api/client";
import {
  adminNavigation,
  type NavigationItem,
  primaryNavigation,
} from "../../app/navigation";

type AdminLayoutProps = {
  children: ReactNode;
  session: SessionInfo;
  onLogout: () => void;
};

type SidebarContentProps = {
  idPrefix: string;
  session: SessionInfo;
  showAccount: boolean;
  onLogout: () => void;
  onNavigate?: () => void;
};

const roleLabels: Record<SessionInfo["principal"]["role"], string> = {
  member: "멤버",
  skill_author: "스킬 작성자",
  admin: "관리자",
  system: "시스템",
};

function NavigationItems({
  items,
  role,
  onNavigate,
}: {
  items: readonly NavigationItem[];
  role: SessionInfo["principal"]["role"];
  onNavigate?: () => void;
}) {
  return items.map((item) => {
    if (item.roles !== undefined && !item.roles.includes(role)) {
      return null;
    }
    if (!item.available) {
      return (
        <span className="nav-item disabled" aria-disabled="true" key={item.path}>
          {item.label}
          <span className="nav-planned">예정</span>
        </span>
      );
    }
    return (
      <NavLink
        className={({ isActive }) => `nav-item${isActive ? " selected" : ""}`}
        end={item.path === "/"}
        key={item.path}
        onClick={onNavigate}
        to={item.path}
      >
        {item.label}
      </NavLink>
    );
  });
}

function SidebarContent({
  idPrefix,
  session,
  showAccount,
  onLogout,
  onNavigate,
}: SidebarContentProps) {
  const isAdmin = session.principal.role === "admin";
  const adminNavigationTitle = `${idPrefix}-admin-navigation-title`;
  return (
    <div className="sidebar-content">
      <Link className="brand" onClick={onNavigate} to="/">
        <span className="brand-mark" aria-hidden="true">P</span>
        <span>Pangi</span>
      </Link>
      <div className="sidebar-navigation">
        <nav aria-label="관리자 메뉴">
          <NavigationItems
            items={primaryNavigation}
            role={session.principal.role}
            onNavigate={onNavigate}
          />
        </nav>
        {isAdmin ? (
          <section className="nav-group" aria-labelledby={adminNavigationTitle}>
            <h2 id={adminNavigationTitle}>관리자 전용</h2>
            <nav aria-label="관리자 전용 메뉴">
              <NavigationItems
                items={adminNavigation}
                role={session.principal.role}
                onNavigate={onNavigate}
              />
            </nav>
          </section>
        ) : null}
      </div>
      {showAccount ? (
        <div className="drawer-account">
          <strong>{session.principal.display_name}</strong>
          <span>{roleLabels[session.principal.role]}</span>
          <button
            className="logout-button"
            onClick={() => {
              onNavigate?.();
              onLogout();
            }}
            type="button"
          >
            로그아웃
          </button>
        </div>
      ) : null}
      <p className="sidebar-note">Organization-operated agent runtime</p>
    </div>
  );
}

export function AdminLayout({ children, session, onLogout }: AdminLayoutProps) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const drawerRef = useRef<HTMLElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const closeDrawer = useCallback(() => setDrawerOpen(false), []);

  useEffect(() => {
    const desktop = window.matchMedia("(min-width: 1024px)");
    const handleDesktop = (event: MediaQueryListEvent) => {
      if (event.matches) {
        closeDrawer();
      }
    };
    desktop.addEventListener("change", handleDesktop);
    return () => desktop.removeEventListener("change", handleDesktop);
  }, [closeDrawer]);

  useEffect(() => {
    if (!drawerOpen) {
      return undefined;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButtonRef.current?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeDrawer();
        return;
      }
      if (event.key !== "Tab") {
        return;
      }
      const focusable = drawerRef.current?.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (focusable === undefined || focusable.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
      if (!window.matchMedia("(min-width: 1024px)").matches) {
        menuButtonRef.current?.focus();
      }
    };
  }, [closeDrawer, drawerOpen]);

  return (
    <div className="admin-shell">
      <aside className="desktop-sidebar" aria-label="관리자 Navigation">
        <SidebarContent
          idPrefix="desktop"
          session={session}
          showAccount={false}
          onLogout={onLogout}
        />
      </aside>
      <div className="admin-page">
        <header className="topbar">
          <button
            aria-controls="mobile-navigation-drawer"
            aria-expanded={drawerOpen}
            aria-label="관리자 메뉴 열기"
            className="menu-button"
            onClick={() => setDrawerOpen(true)}
            ref={menuButtonRef}
            type="button"
          >
            <span aria-hidden="true" />
            <span aria-hidden="true" />
            <span aria-hidden="true" />
          </button>
          <div className="topbar-title">
            <strong>관리자 대시보드</strong>
            <span>안전한 조직 Agent 운영</span>
          </div>
          <div className="topbar-account" aria-label="현재 계정">
            <div>
              <strong>{session.principal.display_name}</strong>
              <span>{roleLabels[session.principal.role]}</span>
            </div>
            <button className="logout-button" onClick={onLogout} type="button">로그아웃</button>
          </div>
        </header>
        <main className="admin-content" id="main-content">{children}</main>
      </div>
      {drawerOpen ? (
        <div className="drawer-layer">
          <button
            aria-hidden="true"
            className="drawer-backdrop"
            onClick={closeDrawer}
            tabIndex={-1}
            type="button"
          />
          <aside
            aria-label="모바일 관리자 메뉴"
            aria-modal="true"
            className="mobile-drawer"
            id="mobile-navigation-drawer"
            ref={drawerRef}
            role="dialog"
          >
            <div className="drawer-header">
              <span>메뉴</span>
              <button
                aria-label="관리자 메뉴 닫기"
                className="drawer-close"
                onClick={closeDrawer}
                ref={closeButtonRef}
                type="button"
              >
                <span aria-hidden="true">×</span>
              </button>
            </div>
            <SidebarContent
              idPrefix="mobile"
              session={session}
              showAccount
              onLogout={onLogout}
              onNavigate={closeDrawer}
            />
          </aside>
        </div>
      ) : null}
    </div>
  );
}
