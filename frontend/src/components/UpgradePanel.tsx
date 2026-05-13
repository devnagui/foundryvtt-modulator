import type { ReactNode } from "react";

type UpgradePanelProps = {
  title: string;
  topFilterRow?: ReactNode;
  systemFilterRow: ReactNode;
  statusFilterRow: ReactNode;
  actionsRow?: ReactNode;
  search: string;
  onSearchChange: (value: string) => void;
  tableHeadAction?: ReactNode;
  tableBody: ReactNode;
  page: number;
  totalPages: number;
  onPrev: () => void;
  onNext: () => void;
};

export function UpgradePanel({
  title,
  topFilterRow,
  systemFilterRow,
  statusFilterRow,
  actionsRow,
  search,
  onSearchChange,
  tableHeadAction,
  tableBody,
  page,
  totalPages,
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
            <th>Status</th>
            <th style={{ textAlign: "right" }}>{tableHeadAction || null}</th>
          </tr>
        </thead>
        <tbody>{tableBody}</tbody>
      </table>
      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <button className="btn secondary" onClick={onPrev}>Prev</button>
        <span style={{ alignSelf: "center" }}>{page} / {totalPages}</span>
        <button className="btn secondary" onClick={onNext}>Next</button>
      </div>
    </article>
  );
}

