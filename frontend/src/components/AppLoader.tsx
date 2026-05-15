type AppLoaderProps = {
  label?: string;
  detail?: string;
  inline?: boolean;
};

export function AppLoader({
  label = "Loading",
  detail = "",
  inline = false,
}: AppLoaderProps) {
  return (
    <div
      className={`app-loader${inline ? " inline" : ""}`}
      role="status"
      aria-live="polite"
      aria-label={detail ? `${label}: ${detail}` : label}
    >
      <span className="app-loader-label">{label}</span>
      {detail ? <span className="app-loader-detail">{detail}</span> : null}
      <span className="app-loader-dots" aria-hidden="true">
        <span />
        <span />
        <span />
      </span>
    </div>
  );
}

