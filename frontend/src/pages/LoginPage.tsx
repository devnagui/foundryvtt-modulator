import { useEffect, useState } from "react";
import { AppLoader } from "../components/AppLoader";
import { api } from "../services/api";

type LoginMode = "setup" | "login";

type LoginPageProps = {
  onAuthenticated: () => void;
};

export function LoginPage({ onAuthenticated }: LoginPageProps) {
  const [mode, setMode] = useState<LoginMode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [locale, setLocale] = useState<string>(() => localStorage.getItem("resolver-locale") || "en");
  const [darkMode, setDarkMode] = useState<boolean>(() => {
    const saved = localStorage.getItem("resolver-theme");
    return saved ? saved === "dark" : true;
  });
  const trimmedUsername = username.trim();
  const passwordChecks = {
    minLen: password.length >= 10,
    upper: /[A-Z]/.test(password),
    lower: /[a-z]/.test(password),
    digit: /\d/.test(password),
    symbol: /[^A-Za-z0-9]/.test(password),
    noUsername: trimmedUsername.length === 0 || !password.toLowerCase().includes(trimmedUsername.toLowerCase()),
    matches: password.length > 0 && password === confirmPassword
  };
  const setupValid =
    passwordChecks.minLen &&
    passwordChecks.upper &&
    passwordChecks.lower &&
    passwordChecks.digit &&
    passwordChecks.symbol &&
    passwordChecks.noUsername &&
    passwordChecks.matches;

  useEffect(() => {
    document.body.classList.toggle("light-mode", !darkMode);
    localStorage.setItem("resolver-theme", darkMode ? "dark" : "light");
  }, [darkMode]);

  useEffect(() => {
    localStorage.setItem("resolver-locale", locale);
  }, [locale]);

  useEffect(() => {
    const run = async () => {
      try {
        const status = await api.authStatus();
        if (status.authenticated) {
          onAuthenticated();
          return;
        }
        setMode(status.passwordConfigured ? "login" : "setup");
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load auth status.");
      } finally {
        setLoading(false);
      }
    };
    void run();
  }, [onAuthenticated]);

  const submit = async () => {
    if (mode === "setup" && !setupValid) {
      setError("Password does not meet all requirements.");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      if (mode === "setup") {
        await api.setup(username, password, confirmPassword);
      } else {
        await api.login(username, password);
      }
      onAuthenticated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Authentication failed.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="auth-shell">
      <section className="auth-card">
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 8 }}>
          <select
            value={locale}
            onChange={(event) => setLocale(event.target.value)}
            aria-label="Language"
            style={{ borderRadius: 10, border: "1px solid var(--line)", padding: "6px 8px", background: "transparent", color: "inherit" }}
          >
            <option value="en">English</option>
            <option value="pt-BR">Português (Brasil)</option>
          </select>
          <button
            type="button"
            className="btn secondary"
            style={{ padding: "6px 10px", borderRadius: 10 }}
            onClick={() => setDarkMode((v) => !v)}
            aria-label="Toggle theme"
          >
            {darkMode ? "Light" : "Dark"}
          </button>
        </div>
        <h1>FoundryVTT Modulator</h1>
        <p>{mode === "setup" ? "Create your admin account" : "Sign in to continue"}</p>
        {loading ? <AppLoader inline label="Loading" detail="Checking authentication status" /> : null}
        {!loading ? (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            <input
              type="text"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="Username"
              autoComplete="username"
            />
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder={mode === "setup" ? "Create password" : "Password"}
              autoComplete={mode === "setup" ? "new-password" : "current-password"}
            />
            {mode === "setup" ? (
              <input
                type="password"
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                placeholder="Confirm password"
                autoComplete="new-password"
              />
            ) : null}
            <ul className="password-rules">
              <li className={passwordChecks.minLen ? "ok" : ""}>At least 10 characters</li>
              <li className={passwordChecks.upper ? "ok" : ""}>One uppercase letter</li>
              <li className={passwordChecks.lower ? "ok" : ""}>One lowercase letter</li>
              <li className={passwordChecks.digit ? "ok" : ""}>One number</li>
              <li className={passwordChecks.symbol ? "ok" : ""}>One symbol</li>
              <li className={passwordChecks.noUsername ? "ok" : ""}>Must not include username</li>
              {mode === "setup" ? <li className={passwordChecks.matches ? "ok" : ""}>Passwords must match</li> : null}
            </ul>
            <button className="btn" type="submit" disabled={submitting || (mode === "setup" && !setupValid)}>
              {submitting ? "Please wait..." : mode === "setup" ? "Create account" : "Login"}
            </button>
            {error ? <p className="error">{error}</p> : null}
          </form>
        ) : null}
      </section>
    </main>
  );
}
