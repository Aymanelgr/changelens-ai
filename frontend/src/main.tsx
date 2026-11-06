import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { isLoggedIn, setToken } from "./api";
import AuditPage from "./pages/AuditPage";
import ChangeRequestPage from "./pages/ChangeRequestPage";
import LoginPage from "./pages/LoginPage";
import ProjectPage from "./pages/ProjectPage";
import ProjectsPage from "./pages/ProjectsPage";
import "./styles.css";

function Protected({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate();
  if (!isLoggedIn()) return <Navigate to="/login" replace />;
  return (
    <>
      <header className="topbar">
        <Link to="/" className="brand">
          ChangeLens <span>AI</span>
        </Link>
        <button
          className="link"
          onClick={() => {
            setToken(null);
            navigate("/login");
          }}
        >
          Déconnexion
        </button>
      </header>
      <main>{children}</main>
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/" element={<Protected><ProjectsPage /></Protected>} />
        <Route path="/projects/:id" element={<Protected><ProjectPage /></Protected>} />
        <Route path="/projects/:id/audit" element={<Protected><AuditPage /></Protected>} />
        <Route path="/change-requests/:id" element={<Protected><ChangeRequestPage /></Protected>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
