import { FC, Fragment, useState } from "react";
import type { ManufacturerContact } from "../types";
import RowMenu from "./RowMenu";

interface Props {
  items: ManufacturerContact[];
  onEdit: (item: ManufacturerContact) => void;
  onDelete: (item: ManufacturerContact) => void;
}

const muted = <span className="cell-muted">—</span>;

const channelClass = (c?: string | null) => {
  if (!c) return "pill pill-neutral";
  const v = c.toLowerCase();
  if (v.includes("web")) return "pill pill-orange";
  if (v.includes("email")) return "pill pill-blue";
  if (v.includes("phone")) return "pill pill-green";
  if (v.includes("portal")) return "pill pill-purple";
  return "pill pill-neutral";
};

const detailField = (label: string, value?: string | null, href?: string) => {
  if (!value) {
    return (
      <div className="detail-field">
        <div className="detail-label">{label}</div>
        <div className="detail-value detail-muted">—</div>
      </div>
    );
  }
  return (
    <div className="detail-field">
      <div className="detail-label">{label}</div>
      <div className="detail-value">
        {href ? (
          <a href={href} target="_blank" rel="noreferrer">
            {value}
          </a>
        ) : (
          value
        )}
      </div>
    </div>
  );
};

const ManufacturerTable: FC<Props> = ({ items, onEdit, onDelete }) => {
  const [expanded, setExpanded] = useState<number | null>(null);

  if (!items.length) {
    return (
      <div className="table-card">
        <div className="empty">
          <div className="empty-title">No manufacturers found</div>
          <div className="empty-sub">Try adjusting your filters.</div>
        </div>
      </div>
    );
  }

  return (
    <div className="table-card">
      <div className="table-scroll">
        <table className="data-table">
          <colgroup>
            <col style={{ width: 36 }} />
            <col />
            <col />
            <col style={{ width: 110 }} />
            <col style={{ width: 200 }} />
            <col style={{ width: 170 }} />
            <col style={{ width: 110 }} />
            <col style={{ width: 64 }} />
          </colgroup>
          <thead>
            <tr>
              <th></th>
              <th>Manufacturer</th>
              <th>Parent / Owner</th>
              <th>Channel</th>
              <th>Contact</th>
              <th>SLA</th>
              <th>HCP Login</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {items.map((m) => {
              const isOpen = expanded === m.id;
              const contactNode = m.mi_phone ? (
                <span>{m.mi_phone}</span>
              ) : m.official_mi_email ? (
                <a href={`mailto:${m.official_mi_email}`}>
                  {m.official_mi_email}
                </a>
              ) : m.mi_web_form_url ? (
                <a href={m.mi_web_form_url} target="_blank" rel="noreferrer">
                  Web Form
                </a>
              ) : (
                muted
              );

              return (
                <Fragment key={m.id}>
                  <tr
                    className={isOpen ? "row-active" : ""}
                    onClick={() => setExpanded(isOpen ? null : m.id)}
                  >
                    <td className="cell-toggle">
                      <svg
                        className={`chev ${isOpen ? "chev-open" : ""}`}
                        viewBox="0 0 10 10"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <path d="M3 1l4 4-4 4" />
                      </svg>
                    </td>
                    <td>
                      <div className="cell-primary">{m.manufacturer}</div>
                    </td>
                    <td className="cell-muted">{m.parent_owner || muted}</td>
                    <td>
                      {m.preferred_channel ? (
                        <span className={channelClass(m.preferred_channel)}>
                          {m.preferred_channel}
                        </span>
                      ) : (
                        muted
                      )}
                    </td>
                    <td>{contactNode}</td>
                    <td className="cell-muted">
                      {m.typical_response_sla || muted}
                    </td>
                    <td>
                      {m.hcp_registration_required === "Yes" ? (
                        <span className="badge badge-warn">Required</span>
                      ) : m.hcp_registration_required === "No" ? (
                        <span className="badge badge-ok">No</span>
                      ) : (
                        muted
                      )}
                    </td>
                    <td
                      className="cell-actions"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <RowMenu onEdit={() => onEdit(m)} onDelete={() => onDelete(m)} />
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="detail-row">
                      <td colSpan={8}>
                        <div className="detail-grid">
                          {detailField(
                            "Official MI Email",
                            m.official_mi_email,
                            m.official_mi_email
                              ? `mailto:${m.official_mi_email}`
                              : undefined
                          )}
                          {detailField(
                            "Team-Verified Email",
                            m.team_verified_email
                          )}
                          {detailField("Email Deliverable", m.email_deliverable)}
                          {detailField(
                            "MI Web Form",
                            m.mi_web_form_url,
                            m.mi_web_form_url || undefined
                          )}
                          {detailField("MI Phone", m.mi_phone)}
                          {detailField("Phone Hours", m.mi_phone_hours)}
                          {detailField("MI Fax", m.mi_fax)}
                          {detailField(
                            "HCP Portal",
                            m.hcp_portal_url,
                            m.hcp_portal_url || undefined
                          )}
                          {detailField(
                            "Last Outreach Date",
                            m.last_outreach_date
                          )}
                          {detailField(
                            "Last Outreach Status",
                            m.last_outreach_status
                          )}
                          {m.notes && (
                            <div className="detail-field detail-full">
                              <div className="detail-label">Notes</div>
                              <div className="detail-value">{m.notes}</div>
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default ManufacturerTable;
