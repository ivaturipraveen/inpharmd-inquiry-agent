import { FC, useMemo } from "react";
import type { ManufacturerContact } from "../types";

interface Props {
  items: ManufacturerContact[];
}

const StatsBar: FC<Props> = ({ items }) => {
  const stats = useMemo(() => {
    const total = items.length;
    const byChannel = items.reduce<Record<string, number>>((acc, m) => {
      const c = m.preferred_channel || "Unknown";
      acc[c] = (acc[c] || 0) + 1;
      return acc;
    }, {});
    const withEmail = items.filter(
      (m) => m.official_mi_email || m.team_verified_email
    ).length;
    const hcpRequired = items.filter(
      (m) => (m.hcp_registration_required || "").toLowerCase() === "yes"
    ).length;
    return { total, byChannel, withEmail, hcpRequired };
  }, [items]);

  return (
    <div className="stats-grid stats-grid-compact">
      <div className="stat-card">
        <div className="stat-label">Total Manufacturers</div>
        <div className="stat-value">{stats.total}</div>
      </div>
      <div className="stat-card">
        <div className="stat-label">Web Form</div>
        <div className="stat-value">{stats.byChannel["Web Form"] || 0}</div>
      </div>
      <div className="stat-card">
        <div className="stat-label">Email Channel</div>
        <div className="stat-value">{stats.byChannel["Email"] || 0}</div>
      </div>
      <div className="stat-card">
        <div className="stat-label">Phone Channel</div>
        <div className="stat-value">{stats.byChannel["Phone"] || 0}</div>
      </div>
      <div className="stat-card">
        <div className="stat-label">HCP Login Required</div>
        <div className="stat-value">{stats.hcpRequired}</div>
      </div>
    </div>
  );
};

export default StatsBar;
