import type { ReactNode } from "react";

type UpgradePanelProps = {
  title: string;
  topFilterRow?: ReactNode;
  systemFilterRow: ReactNode;
  statusFilterRow: ReactNode;
  actionsRow?: ReactNode;
  tableContextLabel?: ReactNode;
  search: string;
  onSearchChange: (value: string) => void;
  tableHeadAction?: ReactNode;
  statusHeadControl?: ReactNode;
  tableBody: ReactNode;
  page: number;
  totalPages: number;
  totalItems?: number;
  onPrev: () => void;
  onNext: () => void;
};

export function UpgradePanel({
  title,
  topFilterRow,
  systemFilterRow,
  statusFilterRow,
  actionsRow,
  tableContextLabel,
  search,
  onSearchChange,
  tableHeadAction,
  statusHeadControl,
  tableBody,
  page,
  totalPages,
  totalItems = 0,
  onPrev,
  onNext,
}: UpgradePanelProps) {
  return (
    <article className="panel">
      <h3>{title}</h3>
      <fieldset className="filters-fieldset">
        <legend>Filters</legend>
        {topFilterRow ? topFilterRow : null}
        {systemFilterRow}
        {statusFilterRow}
      </fieldset>
      {actionsRow ? actionsRow : null}
      {tableContextLabel ? (
        <p style={{ marginTop: 0, marginBottom: 8, color: "var(--muted)", fontSize: 12 }}>{tableContextLabel}</p>
      ) : null}
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
