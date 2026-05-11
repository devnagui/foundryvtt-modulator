type HeaderProps = {
  onLogout: () => Promise<void>;
  onOpenSettings?: () => void;
  settingsState?: "ok" | "warn";
  onStartScan?: () => void;
  scanDisabled?: boolean;
  scanAttention?: boolean;
};

export function Header({
  onLogout,
  onOpenSettings,
  settingsState = "warn",
  onStartScan,
  scanDisabled = true,
  scanAttention = false
}: HeaderProps) {
  return (
    <header className="app-header">
      <div>
        <h1>FoundryVTT Modulator</h1>
        <p>Module Operations Center</p>
      </div>
      <nav>
        {onStartScan ? (
          <button
            className={`btn secondary scan-btn ${scanAttention ? "scan-attention" : ""}`}
            onClick={onStartScan}
            disabled={scanDisabled}
            title={scanDisabled ? "Configure Foundry path first" : "Run initial scan"}
          >
            <span className="icon-wrap" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polygon points="5 3 19 12 5 21 5 3" />
              </svg>
            </span>
            <span>Start Scan</span>
          </button>
        ) : null}
        {onOpenSettings ? (
          <button
            className={`gear-btn ${settingsState}`}
            onClick={onOpenSettings}
            aria-label="Foundry settings"
            title={settingsState === "ok" ? "Foundry path configured" : "Foundry path required"}
          ><span className="icon-wrap" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.82-.33 1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1-1.55 1.7 1.7 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09a1.7 1.7 0 0 0 1.55-1 1.7 1.7 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 8.96 4.6a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.33 1.82 1.7 1.7 0 0 0 1.55 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.55 1z"/></svg></span></button>
        ) : null}
        <button className="btn icon-btn" onClick={() => void onLogout()} aria-label="Logout" title="Logout">
          <span className="icon-wrap" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
              <polyline points="16 17 21 12 16 7" />
              <line x1="21" y1="12" x2="9" y2="12" />
            </svg>
          </span>
        </button>
      </nav>
    </header>
  );
}
