export type AuthStatus = {
  passwordConfigured: boolean;
  authenticated: boolean;
};

export type HealthStatus = {
  ok: boolean;
  foundry: {
    status: string;
    host: string;
    port: number;
    online?: boolean;
  };
};

export type ReportModel = {
  generatedAt?: string;
  targetVersion?: string;
  dataRoot?: string;
  installedSystemVersions?: Record<string, string>;
  worldUsage?: Array<Record<string, unknown>>;
  results?: Array<Record<string, unknown>>;
  view: {
    summary?: { usedModuleCount?: number };
    currentSystemUpgrades?: { rows?: Array<Record<string, unknown>> };
    systemUpgradePlanner?: {
      targets?: Array<Record<string, unknown>>;
      targetsByFoundry?: Record<string, Record<string, unknown>>;
      summary?: Record<string, unknown>;
    };
    backupManagement?: { rows?: Array<Record<string, unknown>>; totalBackupCount?: number; applyHistory?: Array<Record<string, unknown>> };
    unusedModules?: { rows?: Array<Record<string, unknown>>; count?: number };
  };
};

export type ActionSubmitResponse = {
  ok: boolean;
  jobId: string;
  status: string;
  action: string;
};

export type JobStatus = {
  action?: string;
  jobId: string;
  status: "pending" | "running" | "success" | "failed";
  progress: number;
  progressMeta?: Record<string, unknown>;
  result?: Record<string, unknown>;
  error?: string;
};

export type FoundryRootStatus = {
  selected: string;
  normalized?: string;
  valid: boolean;
  message?: string;
};

export type ModuleSourceRow = {
  moduleId: string;
  manifestUrl?: string;
  projectUrl?: string;
  updatedAt?: string;
};

export type ImportHistoryEntry = {
  action?: string;
  profile?: string;
  generatedAt?: string;
  appliedCount?: number;
  skippedCount?: number;
  failureCount?: number;
  planPath?: string;
  failures?: Array<Record<string, unknown>>;
  results?: Record<string, unknown>;
};

export type SuggestModuleContext = {
  targetFoundryVersion?: string;
  installedSystemVersions?: Record<string, string>;
};
export type SuggestModuleBatchInput = {
  moduleId: string;
  manifestUrl?: string;
  projectUrl?: string;
};
export type SuggestModulesBatchResponse = {
  rows?: Array<{ moduleId?: string; suggestion?: Record<string, unknown>; error?: string; errorCode?: string; hint?: string; retryable?: boolean; rawError?: string }>;
};

export type PlanningContextRow = {
  contextKey?: string;
  foundryVersion?: string;
  systemId?: string;
  systemVersion?: string;
  moduleId?: string;
  status?: string;
  hasMissingDependencies?: boolean;
  title?: string;
  installedVersion?: string;
  recommendedVersion?: string;
  reason?: string;
  compatibility?: Record<string, unknown>;
  systemCompatibility?: Record<string, unknown>;
};

function csrfToken(): string {
  const token = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith("mm_csrf="))
    ?.split("=", 2)[1];
  return decodeURIComponent(token || "");
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken(),
      ...(init?.headers || {})
    },
    ...init
  });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const detail = payload?.detail || {};
    const baseMessage = payload?.message || detail?.message || payload?.error || detail?.error || `HTTP ${response.status}`;
    const hint = detail?.hint || payload?.hint || "";
    const message = hint ? `${baseMessage} ${hint}` : baseMessage;
    const err = new Error(String(message)) as Error & { status?: number; payload?: unknown };
    err.status = response.status;
    err.payload = payload;
    throw err;
  }
  return payload as T;
}

export const api = {
  health: () => request<HealthStatus>("/api/v1/health"),
  authStatus: () => request<AuthStatus>("/api/v1/auth/status"),
  setup: (username: string, password: string, confirmPassword: string) =>
    request<{ ok: boolean }>("/api/v1/auth/setup", {
      method: "POST",
      body: JSON.stringify({ username, password, confirmPassword })
    }),
  login: (username: string, password: string) =>
    request<{ ok: boolean }>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password })
    }),
  logout: () => request<{ ok: boolean }>("/api/v1/auth/logout", { method: "POST" }),
  reportV3Model: () => request<ReportModel>("/api/v1/report/v3/model"),
  exportSnapshot: (outputPath = "", includeData = false) =>
    request<{ ok: boolean; path: string; modulesCount: number; systemsCount: number; foundryVersion: string; snapshotData?: Record<string, unknown> }>(
      "/api/v1/report/v3/export-snapshot",
      {
        method: "POST",
        body: JSON.stringify({ outputPath, includeData }),
      }
    ),
  importHistory: (limit = 20) =>
    request<{ ok: boolean; items: ImportHistoryEntry[] }>(`/api/v1/report/v3/import-history?limit=${encodeURIComponent(String(limit))}`),
  planningContext: (foundryVersion: string, systemId = "", systemVersion = "", limit = 5000) =>
    request<{ ok: boolean; scanRunId?: number; count?: number; rows?: PlanningContextRow[] }>(
      `/api/v1/report/v3/planning-context?foundryVersion=${encodeURIComponent(foundryVersion)}&systemId=${encodeURIComponent(systemId)}&systemVersion=${encodeURIComponent(systemVersion)}&limit=${encodeURIComponent(String(limit))}`
    ),
  submitAction: (action: "dry-run" | "apply" | "force-compat" | "cleanup-backups" | "rollback-batch" | "override-from-plan", payload: Record<string, unknown>) =>
    request<ActionSubmitResponse>("/api/v1/actions/submit", {
      method: "POST",
      body: JSON.stringify({ action, payload })
    }),
  jobStatus: (jobId: string) => request<JobStatus>(`/api/v1/actions/jobs/${encodeURIComponent(jobId)}`),
  rollbackPlan: (scanRunId: number) =>
    request<{ ok: boolean; scanRunId: number; generatedAt?: string; targetVersion?: string; modules?: string[]; backupPaths?: string[]; notes?: string }>(
      `/api/v1/actions/rollback-plan?scanRunId=${encodeURIComponent(String(scanRunId))}`
    ),
  moduleHealth: () =>
    request<{ ok: boolean; count?: number; invalidCount?: number; warningCount?: number; rows?: Array<Record<string, unknown>> }>(
      "/api/v1/actions/module-health"
    ),
  rollbackExecute: (scanRunId: number) =>
    request<{ ok: boolean; scanRunId: number; restoredCount?: number; restored?: Array<Record<string, unknown>> }>(
      "/api/v1/actions/rollback-execute",
      {
        method: "POST",
        body: JSON.stringify({ scanRunId })
      }
    ),
  foundryRootStatus: () => request<FoundryRootStatus>("/api/v1/config/foundry-root"),
  setFoundryRoot: (path: string) =>
    request<FoundryRootStatus>("/api/v1/config/foundry-root", {
      method: "POST",
      body: JSON.stringify({ path })
    }),
  resetFoundryRoot: () => request<FoundryRootStatus>("/api/v1/config/foundry-root/reset", { method: "POST" }),
  pickFoundryRoot: () => request<FoundryRootStatus>("/api/v1/config/foundry-root/pick", { method: "POST", body: "{}" }),
  moduleSources: () => request<{ sources: Record<string, ModuleSourceRow> }>("/api/v1/config/module-sources"),
  saveModuleSource: (moduleId: string, manifestUrl: string, projectUrl = "") =>
    request<{ ok: boolean; saved: ModuleSourceRow; suggestion?: Record<string, unknown> }>("/api/v1/config/module-sources", {
      method: "POST",
      body: JSON.stringify({ moduleId, manifestUrl, projectUrl })
    }),
  suggestModule: (manifestUrl: string, context?: SuggestModuleContext, moduleId = "", options?: { forceRefresh?: boolean; projectUrl?: string }) =>
    request<{ suggestion?: Record<string, unknown> }>("/api/v1/actions/suggest-module", {
      method: "POST",
      body: JSON.stringify({
        moduleId,
        manifestUrl,
        projectUrl: options?.projectUrl || "",
        forceRefresh: Boolean(options?.forceRefresh),
        targetFoundryVersion: context?.targetFoundryVersion || "",
        installedSystemVersions: context?.installedSystemVersions || {},
      })
    }),
  suggestModulesBatch: async (modules: SuggestModuleBatchInput[], context?: SuggestModuleContext, options?: { forceRefresh?: boolean }) => {
    const payload = await request(
      "/api/v1/actions/suggest-modules-batch",
      {
        method: "POST",
        body: JSON.stringify({
          modules,
          forceRefresh: Boolean(options?.forceRefresh),
          targetFoundryVersion: context?.targetFoundryVersion || "",
          installedSystemVersions: context?.installedSystemVersions || {}
        })
      }
    );
    return payload as SuggestModulesBatchResponse;
  }
};
