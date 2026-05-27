import { useCallback, useEffect, useMemo, useState } from "react";
import StatsBar from "../components/StatsBar";
import FilterBar, { Filters } from "../components/FilterBar";
import ManufacturerTable from "../components/ManufacturerTable";
import ManufacturerForm from "../components/ManufacturerForm";
import { api } from "../api";
import type { ManufacturerContact, ManufacturerContactInput } from "../types";

const EMPTY_FILTERS: Filters = { q: "", channel: "", hcpRequired: "" };

export default function ManufacturersPage() {
  const [items, setItems] = useState<ManufacturerContact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ManufacturerContact | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setItems(await api.manufacturers.list());
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
    const t = setTimeout(() => setSuccess(null), 2500);
    return () => clearTimeout(t);
  }, [success]);

  const filtered = useMemo(() => {
    const q = filters.q.trim().toLowerCase();
    return items.filter((m) => {
      if (q) {
        const hay = `${m.manufacturer} ${m.parent_owner ?? ""} ${m.notes ?? ""}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      if (filters.channel && m.preferred_channel !== filters.channel) return false;
      if (filters.hcpRequired && m.hcp_registration_required !== filters.hcpRequired)
        return false;
      return true;
    });
  }, [items, filters]);

  const channels = useMemo(() => {
    const set = new Set<string>();
    items.forEach((m) => m.preferred_channel && set.add(m.preferred_channel));
    return Array.from(set).sort();
  }, [items]);

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

  const handleDelete = async (m: ManufacturerContact) => {
    if (!confirm(`Delete ${m.manufacturer}?`)) return;
    try {
      await api.manufacturers.remove(m.id);
      setSuccess(`Deleted ${m.manufacturer}.`);
      load();
    } catch (err: any) {
      setError(err?.message ?? "Failed to delete.");
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

      <StatsBar items={items} />

      <FilterBar
        filters={filters}
        channels={channels}
        count={filtered.length}
        total={items.length}
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
          onEdit={(m) => {
            setEditing(m);
            setModalOpen(true);
          }}
          onDelete={handleDelete}
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
