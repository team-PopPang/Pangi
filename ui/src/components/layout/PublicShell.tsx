import type { ReactNode } from "react";
import { Link } from "react-router-dom";

export function PublicShell({ children }: { children: ReactNode }) {
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
