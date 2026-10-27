import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { AutoDispatchStatus } from "../types";

const fmtDateTime = (s: string | null): string =>
  s ? new Date(s).toLocaleString() : "—";

export default function AutoDispatchToggle() {
  const [status, setStatus] = useState<AutoDispatchStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    api.autoDispatch
      .getStatus()
      .then(setStatus)
      .catch((e: any) => setError(e?.message ?? "Failed to load auto-dispatch status."))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const toggle = useCallback(async () => {
    if (!status || busy) return;
    setBusy(true);
    setError(null);
    try {
      const next = status.enabled
        ? await api.autoDispatch.disable()
        : await api.autoDispatch.enable();
      setStatus(next);
    } catch (e: any) {
      setError(e?.message ?? "Failed to update auto-dispatch setting.");
    } finally {
      setBusy(false);
    }
  }, [status, busy]);

  if (loading) {
    return <div className="auto-dispatch-card auto-dispatch-card-loading">Loading auto-dispatch status…</div>;
  }
  if (!status) {
    return (
      <div className="auto-dispatch-card auto-dispatch-card-error">
        {error ?? "Auto-dispatch status unavailable."}
      </div>
    );
  }

  return (
    <div className={`auto-dispatch-card ${status.enabled ? "auto-dispatch-on" : "auto-dispatch-off"}`}>
      <div className="auto-dispatch-row">
        <button
          type="button"
          role="switch"
          aria-checked={status.enabled}
          className={`auto-dispatch-switch ${status.enabled ? "is-on" : ""}`}
          onClick={toggle}
          disabled={busy}
        >
          <span className="auto-dispatch-switch-knob" />
        </button>
        <div className="auto-dispatch-copy">
          <div className="auto-dispatch-title">
            Autonomous InpharmD Outreach: <strong>{status.enabled ? "ON" : "OFF"}</strong>
          </div>
          <p className="auto-dispatch-desc">
            Only InpharmD inquiries received <strong>while this is ON</strong> are contacted
            automatically — matched manufacturers get emailed or called immediately with no human
            action. Inquiries that existed before enabling it, or that arrive while it's off, are
            never auto-processed, even after you turn this back on.
          </p>
          {status.enabled && status.activated_at && (
            <p className="auto-dispatch-meta">
              Enabled since {fmtDateTime(status.activated_at)}
              {status.updated_by ? ` by ${status.updated_by}` : ""}
            </p>
          )}
          {!status.enabled && status.updated_at && status.updated_by && (
            <p className="auto-dispatch-meta">Last turned off by {status.updated_by}</p>
          )}
        </div>
      </div>
      {error && <div className="auto-dispatch-error">{error}</div>}
    </div>
  );
}
