import { useCallback, useEffect, useMemo, useState } from "react";
import StatusBadge from "../components/StatusBadge";
import InquiryForm from "../components/InquiryForm";
import InquiryDetail from "../components/InquiryDetail";
import ChannelChooser from "../components/ChannelChooser";
import { api } from "../api";
import type { Inquiry, InquiryInput, ManufacturerContact } from "../types";

const STATUS_FILTERS = [
  { value: "", label: "All statuses" },
  { value: "draft", label: "Draft" },
  { value: "email_sent", label: "Email Sent" },
  { value: "email_responded", label: "Email Responded" },
  { value: "call_pending", label: "Call Pending" },
  { value: "call_completed", label: "Call Completed" },
  { value: "needs_attention", label: "Needs Attention" },
  { value: "closed", label: "Closed" },
];

const fmtDate = (s?: string | null) =>
  s ? new Date(s).toLocaleDateString() : "—";

export default function InquiriesPage() {
  const [inquiries, setInquiries] = useState<Inquiry[]>([]);
  const [manufacturers, setManufacturers] = useState<ManufacturerContact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [search, setSearch] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [selected, setSelected] = useState<Inquiry | null>(null);
  const [pendingChoice, setPendingChoice] = useState<Inquiry | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [is, ms] = await Promise.all([
        api.inquiries.list(),
        api.manufacturers.list(),
      ]);
      setInquiries(is);
      setManufacturers(ms);
    } catch (err: any) {
      setError(err?.message ?? "Failed to load inquiries.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!success) return;
    const t = setTimeout(() => setSuccess(null), 2500);
    return () => clearTimeout(t);
  }, [success]);

  // Keep the selected inquiry in sync with the latest list (after actions)
  useEffect(() => {
    if (!selected) return;
    const fresh = inquiries.find((i) => i.id === selected.id);
    if (fresh && fresh !== selected) setSelected(fresh);
  }, [inquiries, selected]);

  // Auto-refresh while anything is in-flight (call dialing, email awaiting reply).
  // Polls every 5s; stops as soon as nothing is transient anymore.
  const hasInFlight = useMemo(
    () =>
      inquiries.some((i) =>
        ["call_pending", "email_sent"].includes(i.status)
      ),
    [inquiries]
  );

  useEffect(() => {
    if (!hasInFlight) return;
    const id = setInterval(() => {
      api.inquiries
        .list()
        .then(setInquiries)
        .catch(() => {});
    }, 5000);
    return () => clearInterval(id);
  }, [hasInFlight]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return inquiries.filter((i) => {
      if (statusFilter && i.status !== statusFilter) return false;
      if (q) {
        const hay = `${i.subject} ${i.question} ${i.manufacturer?.manufacturer ?? ""}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [inquiries, statusFilter, search]);

  const stats = useMemo(() => {
    const counts = inquiries.reduce<Record<string, number>>((acc, i) => {
      acc[i.status] = (acc[i.status] || 0) + 1;
      return acc;
    }, {});
    return {
      total: inquiries.length,
      open: inquiries.filter((i) =>
        ["draft", "email_sent", "call_pending"].includes(i.status)
      ).length,
      awaitingEmail: counts["email_sent"] || 0,
      callQueued: counts["call_pending"] || 0,
      resolved:
        (counts["email_responded"] || 0) +
        (counts["call_completed"] || 0) +
        (counts["closed"] || 0),
    };
  }, [inquiries]);

  const handleCreate = async (data: InquiryInput) => {
    const created = await api.inquiries.create(data);
    setSuccess("Inquiry created. Choose a channel.");
    setFormOpen(false);
    await load();
    setPendingChoice(created);
  };

  const handleAction = async (action: string, payload?: any) => {
    if (!selected) return;
    try {
      let updated: Inquiry;
      switch (action) {
        case "sendEmail":
          updated = await api.inquiries.sendEmail(selected.id);
          setSuccess("Email marked as sent.");
          break;
        case "recordEmailResponse":
          updated = await api.inquiries.recordEmailResponse(selected.id, payload);
          setSuccess("Email response saved.");
          break;
        case "triggerCall":
          updated = await api.inquiries.triggerCall(selected.id);
          setSuccess("Call queued.");
          break;
        case "recordCallResult":
          updated = await api.inquiries.recordCallResult(
            selected.id,
            payload.summary,
            payload.transcript
          );
          setSuccess("Call result saved.");
          break;
        case "close":
          updated = await api.inquiries.close(selected.id);
          setSuccess("Inquiry closed.");
          break;
        case "extractAnswer":
          updated = await api.inquiries.extractAnswer(selected.id);
          setSuccess("Answer extracted from transcript.");
          break;
        default:
          return;
      }
      setSelected(updated);
      load();
    } catch (err: any) {
      setError(err?.message ?? "Action failed.");
    }
  };

  const handleDelete = async () => {
    if (!selected) return;
    await api.inquiries.remove(selected.id);
    setSuccess("Inquiry deleted.");
    setSelected(null);
    load();
  };

  return (
    <>
      <section className="page-head">
        <h1>Inquiries</h1>
        <p>
          Send a question to a manufacturer by email. If they don't respond
          within your fallback window, our voice agent calls them and brings
          the answer back here.
        </p>
      </section>

      <div className="stats-grid stats-grid-4">
        <div className="stat-card">
          <div className="stat-label">Total Inquiries</div>
          <div className="stat-value">{stats.total}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Open</div>
          <div className="stat-value">{stats.open}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Awaiting Email</div>
          <div className="stat-value">{stats.awaitingEmail}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Call Queued</div>
          <div className="stat-value">{stats.callQueued}</div>
        </div>
      </div>

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
              placeholder="Search subject, question, manufacturer…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <select
            className="filter-select"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            {STATUS_FILTERS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
          <div className="filter-spacer" />
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setFormOpen(true)}
          >
            + New Inquiry
          </button>
        </div>
        <div className="filter-meta">
          Showing <strong>{filtered.length}</strong> of {inquiries.length} inquiries
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {success && <div className="success-banner">{success}</div>}

      <div className="table-card">
        {loading ? (
          <div className="empty">
            <div className="empty-title">Loading…</div>
          </div>
        ) : filtered.length === 0 ? (
          <div className="empty">
            <div className="empty-title">No inquiries yet</div>
            <div className="empty-sub">
              Click + New Inquiry to send your first question.
            </div>
          </div>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <colgroup>
                <col style={{ width: 70 }} />
                <col />
                <col style={{ width: "20%" }} />
                <col style={{ width: 150 }} />
                <col style={{ width: 110 }} />
                <col style={{ width: 110 }} />
              </colgroup>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Subject</th>
                  <th>Manufacturer</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th>Fallback</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((i) => (
                  <tr key={i.id} onClick={() => setSelected(i)}>
                    <td className="cell-muted">#{i.id}</td>
                    <td>
                      <div className="cell-primary">{i.subject}</div>
                    </td>
                    <td>{i.manufacturer?.manufacturer ?? "—"}</td>
                    <td>
                      <StatusBadge status={i.status} />
                    </td>
                    <td className="cell-muted">{fmtDate(i.created_at)}</td>
                    <td className="cell-muted">{i.fallback_after_hours}h</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {formOpen && (
        <InquiryForm
          manufacturers={manufacturers}
          onClose={() => setFormOpen(false)}
          onSubmit={handleCreate}
        />
      )}

      {selected && (
        <InquiryDetail
          inquiry={selected}
          onClose={() => setSelected(null)}
          onAction={handleAction}
          onDelete={handleDelete}
        />
      )}

      {pendingChoice && (
        <ChannelChooser
          inquiry={pendingChoice}
          onClose={() => setPendingChoice(null)}
          onSendEmail={async () => {
            const updated = await api.inquiries.sendEmail(pendingChoice.id);
            setSuccess(`Email queued for ${updated.manufacturer?.manufacturer ?? ""}.`);
            setPendingChoice(null);
            await load();
            setSelected(updated);
          }}
          onCallTriggered={() => {
            setSuccess("Call placed. The agent is dialing now.");
            setPendingChoice(null);
            load();
          }}
          onTestCallTriggered={(phone) => {
            // Keep the channel chooser OPEN so the user can still send the
            // real email or place the real call after verifying via test.
            setSuccess(`Test call dialing ${phone}. Your phone should ring shortly.`);
          }}
        />
      )}
    </>
  );
}
