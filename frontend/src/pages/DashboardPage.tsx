import { useEffect, useState } from "react";
import { Header } from "../components/Header";
import { api, type AuthStatus, type HealthStatus } from "../services/api";

type DashboardPageProps = {
  onLoggedOut: () => void;
};

export function DashboardPage({ onLoggedOut }: DashboardPageProps) {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = async () => {
    setError("");
    try {
      const [healthPayload, authPayload] = await Promise.all([api.health(), api.authStatus()]);
      if (!authPayload.authenticated) {
        onLoggedOut();
        return;
      }
      setHealth(healthPayload);
      setAuth(authPayload);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load data.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const logout = async () => {
    await api.logout();
    onLoggedOut();
  };

  return (
    <main className="dashboard-shell">
      <Header onLogout={logout} />
      <section className="panel-grid">
        <article className="panel">
          <h2>Status</h2>
          {loading ? <p>Loading...</p> : null}
          {error ? <p className="error">{error}</p> : null}
          {health ? (
            <ul>
              <li>API: {health.ok ? "online" : "offline"}</li>
              <li>Foundry: {health.foundry.status}</li>
              <li>Host: {health.foundry.host}:{health.foundry.port}</li>
              <li>Authenticated: {String(auth?.authenticated ?? false)}</li>
            </ul>
          ) : null}
          <button className="btn secondary" onClick={() => void refresh()}>Refresh</button>
        </article>

        <article className="panel">
          <h2>Migration Track</h2>
          <p>This React UI is now wired to the existing backend.</p>
          <ul>
            <li>Auth setup/login/logout via <code>/api/v1</code></li>
            <li>Health and session status</li>
            <li>React Report v3 view with first-run flow and tabs</li>
          </ul>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <a className="btn" href="/app/report">Open React Report v3</a>
            <a className="btn secondary" href="/api/report/v3">Open Legacy Report v3</a>
          </div>
        </article>
      </section>
    </main>
  );
}
