import { useCallback, useEffect, useMemo, useState } from "react";
import StatsBar from "../components/StatsBar";
import FilterBar, { Filters } from "../components/FilterBar";
import ManufacturerTable from "../components/ManufacturerTable";
import ManufacturerForm from "../components/ManufacturerForm";
import { api } from "../api";
import type { ManufacturerContact, ManufacturerContactInput } from "../types";
import { TOAST_DURATION_MS } from "../utils/uiConstants";

const EMPTY_FILTERS: Filters = { q: "", channel: "", hcpRequired: "" };

type ViewTab = "active" | "archived";

export default function ManufacturersPage() {
  const [items, setItems] = useState<ManufacturerContact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ManufacturerContact | null>(null);
  const [view, setView] = useState<ViewTab>("active");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setItems(await api.manufacturers.list({ includeArchived: true }));
    } catch (err: any) {
      setError(err?.message ?? "Failed to load manufacturers.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!success) return;
    const t = setTimeout(() => setSuccess(null), TOAST_DURATION_MS);
    return () => clearTimeout(t);
  }, [success]);

  useEffect(() => {
    if (!error) return;
    const t = setTimeout(() => setError(null), TOAST_DURATION_MS);
    return () => clearTimeout(t);
  }, [error]);

  const filtered = useMemo(() => {
    const q = filters.q.trim().toLowerCase();
    return items.filter((m) => {
      const isArchived = !!m.archived_at;
      if (view === "archived" ? !isArchived : isArchived) return false;
      if (q) {
        const hay = `${m.manufacturer} ${m.parent_owner ?? ""} ${m.notes ?? ""}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      if (filters.channel && m.preferred_channel !== filters.channel) return false;
      if (filters.hcpRequired && m.hcp_registration_required !== filters.hcpRequired)
        return false;
      return true;
    });
  }, [items, filters, view]);

  // Stats/filter-dropdown context stays scoped to active manufacturers —
  // archived entries shouldn't skew "Total Manufacturers" or the channel list.
  const activeItems = useMemo(() => items.filter((m) => !m.archived_at), [items]);

  const channels = useMemo(() => {
    const set = new Set<string>();
    activeItems.forEach((m) => m.preferred_channel && set.add(m.preferred_channel));
    return Array.from(set).sort();
  }, [activeItems]);

  const handleSubmit = async (data: ManufacturerContactInput) => {
    if (editing) {
      await api.manufacturers.update(editing.id, data);
      setSuccess(`Updated ${data.manufacturer}.`);
    } else {
      await api.manufacturers.create(data);
      setSuccess(`Added ${data.manufacturer}.`);
    }
    setModalOpen(false);
    load();
  };

  const handleArchive = async (m: ManufacturerContact) => {
    if (!confirm(`Archive ${m.manufacturer}? Existing inquiries and scheduled outreach are unaffected — you can restore it anytime.`)) return;
    try {
      await api.manufacturers.archive(m.id);
      setSuccess(`Archived ${m.manufacturer}.`);
      load();
    } catch (err: any) {
      setError(err?.message ?? "Failed to archive.");
    }
  };

  const handleRestore = async (m: ManufacturerContact) => {
    try {
      await api.manufacturers.restore(m.id);
      setSuccess(`Restored ${m.manufacturer}.`);
      load();
    } catch (err: any) {
      setError(err?.message ?? "Failed to restore.");
    }
  };

  return (
    <>
      <section className="page-head">
        <h1>Manufacturer MI Contacts</h1>
        <p>
          Trusted directory of pharmaceutical medical-information channels —
          curated for pharmacy operations.
        </p>
      </section>

      <StatsBar items={activeItems} />

      <div className="outreach-tabs">
        <button
          type="button"
          className={`outreach-tab ${view === "active" ? "outreach-tab-active" : ""}`}
          onClick={() => setView("active")}
        >
          Active
        </button>
        <button
          type="button"
          className={`outreach-tab ${view === "archived" ? "outreach-tab-active" : ""}`}
          onClick={() => setView("archived")}
        >
          Archived
        </button>
      </div>

      <FilterBar
        filters={filters}
        channels={channels}
        count={filtered.length}
        total={view === "archived" ? items.filter((m) => m.archived_at).length : activeItems.length}
        onChange={setFilters}
        onReset={() => setFilters(EMPTY_FILTERS)}
        onAdd={() => {
          setEditing(null);
          setModalOpen(true);
        }}
      />

      {error && <div className="error-banner">{error}</div>}
      {success && <div className="success-banner">{success}</div>}

      {loading ? (
        <div className="table-card">
          <div className="empty">
            <div className="empty-title">Loading…</div>
          </div>
        </div>
      ) : (
        <ManufacturerTable
          items={filtered}
          view={view}
          onEdit={(m) => {
            setEditing(m);
            setModalOpen(true);
          }}
          onArchive={handleArchive}
          onRestore={handleRestore}
        />
      )}

      {modalOpen && (
        <ManufacturerForm
          initial={editing}
          onClose={() => setModalOpen(false)}
          onSubmit={handleSubmit}
        />
      )}
    </>
  );
}
