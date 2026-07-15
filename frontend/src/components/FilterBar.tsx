import { FC } from "react";

export interface Filters {
  q: string;
  channel: string;
  hcpRequired: string;
}

interface Props {
  filters: Filters;
  channels: string[];
  count: number;
  total: number;
  onChange: (next: Filters) => void;
  onReset: () => void;
  onAdd: () => void;
}

const FilterBar: FC<Props> = ({
  filters,
  channels,
  count,
  total,
  onChange,
  onReset,
  onAdd,
}) => {
  const isFiltered =
    filters.q !== "" || filters.channel !== "" || filters.hcpRequired !== "";

  return (
    <div className="filter-bar">
      <div className="filter-row">
        <div className="search-wrap">
          <svg
            className="search-icon"
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="9" cy="9" r="6" />
            <path d="m17 17-3.5-3.5" />
          </svg>
          <input
            className="search-input"
            placeholder="Search manufacturer, parent, or notes…"
            value={filters.q}
            onChange={(e) => onChange({ ...filters, q: e.target.value })}
          />
          {filters.q && (
            <button
              type="button"
              className="search-clear"
              onClick={() => onChange({ ...filters, q: "" })}
              aria-label="Clear search"
            >
              ×
            </button>
          )}
        </div>

        <select
          className="filter-select"
          value={filters.channel}
          onChange={(e) => onChange({ ...filters, channel: e.target.value })}
        >
          <option value="">All channels</option>
          {channels.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>

        <select
          className="filter-select"
          value={filters.hcpRequired}
          onChange={(e) =>
            onChange({ ...filters, hcpRequired: e.target.value })
          }
        >
          <option value="">HCP Login: Any</option>
          <option value="Yes">HCP Login: Yes</option>
          <option value="No">HCP Login: No</option>
        </select>

        {isFiltered && (
          <button type="button" className="btn-link" onClick={onReset}>
            Clear filters
          </button>
        )}

        <div className="filter-spacer" />

        <button type="button" className="btn btn-primary" onClick={onAdd}>
          + Add Manufacturer
        </button>
      </div>

      <div className="filter-meta">
        Showing <strong>{count}</strong> of {total} manufacturers
      </div>
    </div>
  );
};

export default FilterBar;
