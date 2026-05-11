import { useEffect, useMemo, useState } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { DashboardPage } from "./pages/DashboardPage";
import { LoginPage } from "./pages/LoginPage";
import { ReportPage } from "./pages/ReportPage";
import { api } from "./services/api";

export function App() {
  const navigate = useNavigate();
  const [sessionReady, setSessionReady] = useState<boolean | null>(null);

  useEffect(() => {
    const run = async () => {
      try {
        const status = await api.authStatus();
        setSessionReady(Boolean(status.authenticated));
      } catch {
        setSessionReady(false);
      }
    };
    void run();
  }, []);

  const handlers = useMemo(
    () => ({
      onAuthenticated: () => {
        setSessionReady(true);
        navigate("/app", { replace: true });
      },
      onLoggedOut: () => {
        setSessionReady(false);
        navigate("/", { replace: true });
      }
    }),
    [navigate]
  );

  if (sessionReady === null) {
    return null;
  }

  return (
    <Routes>
      <Route path="/" element={<LoginPage onAuthenticated={handlers.onAuthenticated} />} />
      <Route
        path="/app"
        element={
          sessionReady ? (
            <DashboardPage onLoggedOut={handlers.onLoggedOut} />
          ) : (
            <Navigate to="/" replace />
          )
        }
      />
      <Route
        path="/app/report"
        element={
          sessionReady ? (
            <ReportPage onLoggedOut={handlers.onLoggedOut} />
          ) : (
            <Navigate to="/" replace />
          )
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
