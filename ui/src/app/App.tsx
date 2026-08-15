import { Route, Routes } from "react-router-dom";

import { AuthenticatedApp } from "../features/auth/AuthenticatedApp";
import { BootstrapAdminPage } from "../features/auth/BootstrapAdminPage";
import { LoginPage } from "../features/auth/LoginPage";

export function App() {
  return (
    <Routes>
      <Route path="/bootstrap" element={<BootstrapAdminPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="*" element={<AuthenticatedApp />} />
    </Routes>
  );
}
