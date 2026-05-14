import type { ReactNode } from "react";

type UpdatePathWithRefreshProps = {
  content: ReactNode;
  hasError?: boolean;
  refreshing?: boolean;
  disabled?: boolean;
  title?: string;
  onRefresh: () => void;
};

export function UpdatePathWithRefresh({
  content,
  hasError = false,
  refreshing = false,
  disabled = false,
  title = "Refresh versions from source (GitHub/GitLab)",
  onRefresh,
}: UpdatePathWithRefreshProps) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span>{content}</span>
      <button
        className="btn secondary"
        style={{
          background: hasError ? "#ef4444" : "#f59e0b",
          color: hasError ? "#fff" : "#111827",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 28,
          height: 28,
          padding: 0,
          minWidth: 28,
        }}
        disabled={disabled}
        title={title}
        aria-label={refreshing ? "Refreshing module versions" : "Refresh module versions"}
        onClick={onRefresh}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="16" height="16" aria-hidden="true">
          <polyline points="23 4 23 10 17 10" />
          <polyline points="1 20 1 14 7 14" />
          <path d="M3.51 9a9 9 0 0 1 14.13-3.36L23 10M1 14l5.36 4.36A9 9 0 0 0 20.49 15" />
        </svg>
      </button>
    </span>
  );
}
