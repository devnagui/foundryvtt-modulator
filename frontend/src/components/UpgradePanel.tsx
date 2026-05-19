import type { ReactNode } from "react";
import { AppLoader } from "./AppLoader";

type UpgradePanelProps = {
  title: string;
  titleExtra?: ReactNode;
  topFilterRow?: ReactNode;
  systemFilterRow: ReactNode;
  statusFilterRow: ReactNode;
  wrapFiltersInFieldset?: boolean;
  actionsRow?: ReactNode;
  tableContextLabel?: ReactNode;
  search: string;
  onSearchChange: (value: string) => void;
  tableHeadAction?: ReactNode;
  statusHeadControl?: ReactNode;
  tableBody: ReactNode;
  tableLoading?: boolean;
  tableLoadingText?: string;
  page: number;
  totalPages: number;
  totalItems?: number;
  onPrev: () => void;
  onNext: () => void;
};

export function UpgradePanel({
  title,
  titleExtra,
  topFilterRow,
  systemFilterRow,
  statusFilterRow,
  wrapFiltersInFieldset = true,
  actionsRow,
  tableContextLabel,
  search,
  onSearchChange,
  tableHeadAction,
  statusHeadControl,
  tableBody,
  tableLoading = false,
  tableLoadingText = "Loading...",
  page,
  totalPages,
  totalItems = 0,
  onPrev,
  onNext,
}: UpgradePanelProps) {
  return (
    <article className="panel">
      <h3 style={{ display: "flex", alignItems: "center", gap: 8 }}>{title}{titleExtra || null}</h3>
      {wrapFiltersInFieldset ? (
        <fieldset className="filters-fieldset">
          <legend>Filters</legend>
          {topFilterRow ? topFilterRow : null}
          {systemFilterRow}
          {statusFilterRow}
        </fieldset>
      ) : (
        <div style={{ display: "grid", gap: 8, marginBottom: 10 }}>
          {topFilterRow ? topFilterRow : null}
          {systemFilterRow}
          {statusFilterRow}
        </div>
      )}
      {actionsRow ? actionsRow : null}
      {tableContextLabel ? (
        <p style={{ marginTop: 0, marginBottom: 8, color: "var(--muted)", fontSize: 12 }}>{tableContextLabel}</p>
      ) : null}
      <div style={{ position: "relative" }}>
        <table className="report-table">
          <thead>
            <tr>
              <th>
                <input
                  type="search"
                  placeholder="Name Search"
                  value={search}
                  onChange={(event) => onSearchChange(event.target.value)}
                />
              </th>
              <th>Update Path</th>
              <th>
                <div style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <span>Status</span>
                  {statusHeadControl || null}
                </div>
              </th>
              <th style={{ textAlign: "right" }}>{tableHeadAction || null}</th>
            </tr>
          </thead>
          <tbody>{tableBody}</tbody>
        </table>
        {tableLoading ? (
          <div
            style={{
              position: "absolute",
              inset: 0,
              background: "rgba(2, 6, 23, 0.78)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              zIndex: 2,
              borderRadius: 10,
            }}
          >
            <AppLoader label="Calculating compatibility" detail={tableLoadingText} />
          </div>
        ) : null}
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 8, justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ color: "var(--muted)" }}>Total: {Number(totalItems || 0)}</span>
        <div style={{ display: "inline-flex", gap: 8, alignItems: "center", justifyContent: "flex-end" }}>
          <button className="btn secondary" onClick={onPrev}>Prev</button>
          <span>{page} / {totalPages}</span>
          <button className="btn secondary" onClick={onNext}>Next</button>
        </div>
      </div>
    </article>
  );
}
