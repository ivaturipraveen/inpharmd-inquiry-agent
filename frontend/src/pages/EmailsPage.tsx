import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Inquiry } from "../types";

/* ----------------------------- helpers ----------------------------- */

const READ_KEY = "inpharmd_email_read"; // localStorage map of inquiryId -> true
const readReadSet = (): Set<number> => {
  try {
    const raw = localStorage.getItem(READ_KEY);
    if (!raw) return new Set();
    const arr: number[] = JSON.parse(raw);
    return new Set(arr);
  } catch {
    return new Set();
  }
};
const persistReadSet = (s: Set<number>) =>
  localStorage.setItem(READ_KEY, JSON.stringify(Array.from(s)));

const fmtListDate = (s?: string | null) => {
  if (!s) return "";
  const d = new Date(s);
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) {
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  const sameYear = d.getFullYear() === now.getFullYear();
  return d.toLocaleDateString([], {
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
  });
};
const fmtLong = (s?: string | null) =>
  s ? new Date(s).toLocaleString([], {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }) : "—";

const preview = (text?: string | null, n = 110) => {
  if (!text) return "";
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length > n ? clean.slice(0, n - 1) + "…" : clean;
};

const initials = (name?: string | null) => {
  if (!name) return "?";
  // Strip parenthetical tokens like "(TEST)" and keep only words that begin
  // with a letter/digit — so "Yanthraa (TEST)" → "YA", "Eli Lilly" → "EL".
  const words = name
    .trim()
    .split(/\s+/)
    .filter((w) => /^[A-Za-z0-9]/.test(w))
    .slice(0, 2);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return words.map((w) => w[0].toUpperCase()).join("");
};

// Deterministic professional color per sender — muted tones that coexist with
// the InpharmD orange brand. No primary blues/cyans.
const AVATAR_PALETTE = [
  "#475569", // slate
  "#7c6b54", // warm taupe
  "#92675a", // muted terracotta
  "#5e7060", // sage
  "#6b5b73", // muted plum
  "#4f6470", // steel
  "#8a6a3b", // bronze
  "#5b6470", // graphite
];
const avatarColor = (name?: string | null) => {
  const key = (name ?? "").trim();
  if (!key) return AVATAR_PALETTE[0];
  let hash = 0;
  for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  return AVATAR_PALETTE[hash % AVATAR_PALETTE.length];
};

const hasEmailActivity = (i: Inquiry) =>
  Boolean(i.email_sent_at || i.email_response_at || i.email_response);

type FolderKey = "all" | "unresponded";

const FOLDERS: { key: FolderKey; label: string; icon: string }[] = [
  { key: "all", label: "All Mail", icon: "M3 7l9 6 9-6M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V7M3 7l9-4 9 4" },
  { key: "unresponded", label: "Unresponded", icon: "M12 8v4l3 2m6-2a9 9 0 11-18 0 9 9 0 0118 0z" },
];

/* ----------------------------- component ----------------------------- */

export default function EmailsPage() {
  const [inquiries, setInquiries] = useState<Inquiry[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [folder, setFolder] = useState<FolderKey>("all");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [readSet, setReadSet] = useState<Set<number>>(() => readReadSet());

  const load = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true); else setLoading(true);
    setError(null);
    try {
      const all = await api.inquiries.list();
      setInquiries(all);
    } catch (e: any) {
      setError(e?.message ?? "Failed to load emails.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const id = setInterval(() => load(true), 15000);
    return () => clearInterval(id);
  }, [load]);

  const markRead = useCallback((id: number) => {
    setReadSet((prev) => {
      if (prev.has(id)) return prev;
      const next = new Set(prev);
      next.add(id);
      persistReadSet(next);
      return next;
    });
  }, []);

  const markUnread = useCallback((id: number) => {
    setReadSet((prev) => {
      if (!prev.has(id)) return prev;
      const next = new Set(prev);
      next.delete(id);
      persistReadSet(next);
      return next;
    });
  }, []);

  // "Unread" rule: any thread we haven't manually opened, where the latest
  // event is an inbound reply OR we're still waiting for one.
  const isUnread = useCallback((i: Inquiry) => {
    if (readSet.has(i.id)) return false;
    return i.status === "email_sent" || Boolean(i.email_response_at);
  }, [readSet]);

  const allEmails = useMemo(
    () => inquiries.filter(hasEmailActivity),
    [inquiries]
  );

  const counts = useMemo(() => ({
    all: allEmails.length,
    unresponded: allEmails.filter((i) => i.status === "email_sent").length,
  }), [allEmails]);

  // Header stats — sent / replied / awaiting / response-rate / avg response time / today
  const stats = useMemo(() => {
    const sent = allEmails.length;
    const replied = allEmails.filter((i) => i.email_response_at).length;
    const awaiting = allEmails.filter((i) => i.status === "email_sent").length;
    const rate = sent ? Math.round((replied / sent) * 100) : 0;

    // Avg response time (hours) — only for replied threads with both timestamps.
    const replyDurations: number[] = [];
    for (const i of allEmails) {
      if (i.email_sent_at && i.email_response_at) {
        const ms = new Date(i.email_response_at).getTime() - new Date(i.email_sent_at).getTime();
        if (ms > 0) replyDurations.push(ms / 3_600_000);
      }
    }
    const avgHours = replyDurations.length
      ? replyDurations.reduce((a, b) => a + b, 0) / replyDurations.length
      : null;

    // Activity in the last 24h
    const now = Date.now();
    const last24h = (s?: string | null) =>
      s ? now - new Date(s).getTime() < 24 * 3_600_000 : false;
    const sentToday = allEmails.filter((i) => last24h(i.email_sent_at)).length;
    const repliedToday = allEmails.filter((i) => last24h(i.email_response_at)).length;

    return { sent, replied, awaiting, rate, avgHours, sentToday, repliedToday };
  }, [allEmails]);

  const formatHours = (h: number | null) => {
    if (h === null) return "—";
    if (h < 1) return `${Math.max(1, Math.round(h * 60))}m`;
    if (h < 24) return `${h.toFixed(1)}h`;
    return `${(h / 24).toFixed(1)}d`;
  };

  const threads = useMemo(() => {
    const byFolder = allEmails.filter((i) => {
      if (folder === "unresponded") return i.status === "email_sent";
      return true; // "all"
    });
    const q = search.trim().toLowerCase();
    const filtered = q
      ? byFolder.filter((i) =>
          `${i.subject} ${i.question} ${i.manufacturer?.manufacturer ?? ""} ${i.email_response ?? ""} ${i.final_answer ?? ""}`
            .toLowerCase()
            .includes(q)
        )
      : byFolder;
    return [...filtered].sort((a, b) => {
      const ta = new Date(a.email_response_at || a.email_sent_at || a.created_at || 0).getTime();
      const tb = new Date(b.email_response_at || b.email_sent_at || b.created_at || 0).getTime();
      return tb - ta;
    });
  }, [allEmails, folder, search, isUnread, readSet.size]);

  // Auto-select first thread when nothing valid is selected
  useEffect(() => {
    if (threads.length === 0) { setSelectedId(null); return; }
    if (selectedId === null || !threads.find((t) => t.id === selectedId)) {
      setSelectedId(threads[0].id);
      markRead(threads[0].id);
    }
  }, [threads, selectedId, markRead]);

  const selected = useMemo(
    () => threads.find((t) => t.id === selectedId) ?? null,
    [threads, selectedId]
  );

  const handleSelect = (id: number) => {
    setSelectedId(id);
    markRead(id);
  };

  return (
    <>
      <div className="gm-stats">
        <StatTile
          label="Emails Sent"
          value={stats.sent}
          sub={stats.sentToday ? `+${stats.sentToday} in last 24h` : "no new sends in 24h"}
          tone="neutral"
        />
        <StatTile
          label="Replies Received"
          value={stats.replied}
          sub={stats.repliedToday ? `+${stats.repliedToday} in last 24h` : "no new replies in 24h"}
          tone="good"
        />
        <StatTile
          label="Awaiting Reply"
          value={stats.awaiting}
          sub={stats.awaiting > 0 ? "manufacturers haven't responded yet" : "all caught up"}
          tone={stats.awaiting > 0 ? "warn" : "good"}
        />
        <StatTile
          label="Response Rate"
          value={`${stats.rate}%`}
          sub={`${stats.replied} of ${stats.sent || 0} threads replied`}
          tone={stats.rate >= 70 ? "good" : stats.rate >= 40 ? "warn" : "bad"}
        />
        <StatTile
          label="Avg Response Time"
          value={formatHours(stats.avgHours)}
          sub={stats.avgHours === null ? "no replies yet" : "from send → reply"}
          tone="neutral"
        />
      </div>

      <section className="gm-shell">
        {/* Left rail: folders */}
      <aside className="gm-rail">
        <button type="button" className="gm-compose" disabled title="Compose (coming soon)">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 5v14M5 12h14" />
          </svg>
          <span>Compose</span>
        </button>
        <nav className="gm-folders" aria-label="Folders">
          {FOLDERS.map((f) => (
            <button
              key={f.key}
              type="button"
              className={`gm-folder ${folder === f.key ? "gm-folder-active" : ""}`}
              onClick={() => setFolder(f.key)}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d={f.icon} />
              </svg>
              <span className="gm-folder-label">{f.label}</span>
              {counts[f.key] > 0 && (
                <span className="gm-folder-count">{counts[f.key]}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="gm-rail-foot">druginfo@inpharmd.com</div>
      </aside>

      {/* Middle: thread list */}
      <section className="gm-list-pane">
        <div className="gm-toolbar">
          <div className="gm-search">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <circle cx="9" cy="9" r="6" />
              <path d="m17 17-3.5-3.5" />
            </svg>
            <input
              placeholder="Search mail"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          {refreshing && <span className="gm-refreshing">syncing…</span>}
        </div>

        <div className="gm-list">
          {loading ? (
            <div className="gm-empty">Loading…</div>
          ) : error ? (
            <div className="gm-empty gm-empty-error">{error}</div>
          ) : threads.length === 0 ? (
            <div className="gm-empty">
              <div className="gm-empty-title">No emails here</div>
              <div className="gm-empty-sub">Try a different folder or search term.</div>
            </div>
          ) : (
            threads.map((t) => {
              const sender = t.manufacturer?.manufacturer ?? "Unknown";
              const unread = isUnread(t);
              const lastDate = t.email_response_at || t.email_sent_at || t.created_at;
              const snippet = preview(
                t.email_response || t.final_answer || t.question,
                90
              );
              return (
                <button
                  key={t.id}
                  type="button"
                  className={`gm-row ${selectedId === t.id ? "gm-row-active" : ""} ${unread ? "gm-row-unread" : ""}`}
                  onClick={() => handleSelect(t.id)}
                >
                  <div
                    className="gm-avatar"
                    style={{ background: avatarColor(sender) }}
                    aria-hidden
                  >
                    {initials(sender)}
                  </div>
                  <div className="gm-row-body">
                    <div className="gm-row-line1">
                      <span className="gm-row-sender">{sender}</span>
                      <span className="gm-row-date">{fmtListDate(lastDate)}</span>
                    </div>
                    <div className="gm-row-line2">
                      <span className="gm-row-subject">{t.subject}</span>
                      <span className="gm-row-snippet"> — {snippet}</span>
                    </div>
                    {(t.status === "email_sent" || t.status === "closed") && (
                      <div className="gm-row-chips">
                        {t.status === "email_sent" && (
                          <span className="gm-chip gm-chip-awaiting">Awaiting reply</span>
                        )}
                        {t.status === "closed" && (
                          <span className="gm-chip gm-chip-closed">Closed</span>
                        )}
                      </div>
                    )}
                  </div>
                </button>
              );
            })
          )}
        </div>
      </section>

      {/* Right: reader */}
      <section className="gm-reader">
        {!selected ? (
          <div className="gm-empty gm-empty-center">
            <div className="gm-empty-title">No conversation selected</div>
            <div className="gm-empty-sub">Pick a thread from the list.</div>
          </div>
        ) : (
          <ThreadReader
            inquiry={selected}
            onMarkUnread={() => markUnread(selected.id)}
          />
        )}
      </section>
      </section>
    </>
  );
}

/* ----------------------------- stat tile ----------------------------- */

interface StatTileProps {
  label: string;
  value: string | number;
  sub?: string;
  tone?: "neutral" | "good" | "warn" | "bad";
}

function StatTile({ label, value, sub, tone = "neutral" }: StatTileProps) {
  return (
    <div className={`gm-stat gm-stat-${tone}`}>
      <div className="gm-stat-label">{label}</div>
      <div className="gm-stat-value">{value}</div>
      {sub && <div className="gm-stat-sub">{sub}</div>}
    </div>
  );
}


/* ----------------------------- reader ----------------------------- */

interface ReaderProps {
  inquiry: Inquiry;
  onMarkUnread: () => void;
}

function ThreadReader({ inquiry, onMarkUnread }: ReaderProps) {
  const m = inquiry.manufacturer;
  const to = m?.official_mi_email || m?.team_verified_email || "—";
  const hasReply = Boolean(inquiry.email_response || inquiry.email_response_at);
  const replyBody = inquiry.email_response || inquiry.final_answer;

  return (
    <div className="gm-thread">
      <header className="gm-thread-head">
        <h2 className="gm-thread-subject">{inquiry.subject}</h2>
        <div className="gm-thread-tags">
          <span className={`gm-chip gm-chip-${
            hasReply ? "replied" : inquiry.status === "email_sent" ? "awaiting" : "closed"
          }`}>
            {hasReply ? "Responded" : inquiry.status === "email_sent" ? "Awaiting reply" : "Closed"}
          </span>
          <span className="gm-thread-id-chip">#{inquiry.id}</span>
          <button
            type="button"
            className="gm-icon-btn gm-icon-btn-sm"
            onClick={onMarkUnread}
            title="Mark as unread"
            aria-label="Mark as unread"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 6 12 13 2 6" />
              <rect x="2" y="4" width="20" height="16" rx="2" />
            </svg>
          </button>
        </div>
      </header>

      {/* Outgoing — InpharmD -> manufacturer */}
      <Message
        avatarColor={avatarColor("InpharmD")}
        avatarInitials="IN"
        senderName="InpharmD Medical Information"
        senderHandle="medinfo@inpharmd.com"
        recipientLabel={`to ${to}`}
        timestamp={fmtLong(inquiry.email_sent_at || inquiry.created_at)}
        body={inquiry.question}
        direction="out"
      />

      {/* Incoming — manufacturer -> us. We show three distinct blocks when a
          PDF is attached: direct reply body, AI summary of the PDF, then the
          PDF link at the bottom. */}
      {hasReply ? (
        <>
          <Message
            avatarColor={avatarColor(m?.manufacturer ?? "Manufacturer")}
            avatarInitials={initials(m?.manufacturer)}
            senderName={`${m?.manufacturer ?? "Manufacturer"} Medical Information`}
            senderHandle={to}
            recipientLabel="to InpharmD"
            timestamp={fmtLong(inquiry.email_response_at)}
            body={replyBody ?? ""}
            direction="in"
            highlight
            inboundAttachments={inquiry.inbound_attachments?.length ? inquiry.inbound_attachments : undefined}
            summary={inquiry.pdf_summary}
            pdfUrl={inquiry.pdf_url}
            pdfFilename={inquiry.pdf_filename}
          />
        </>
      ) : (
        <div className="gm-awaiting">
          <div className="gm-awaiting-dot" />
          <div>
            <div className="gm-awaiting-title">Waiting for {m?.manufacturer ?? "manufacturer"} to reply</div>
            <div className="gm-awaiting-sub">
              Voice agent will auto-fall back after{" "}
              <strong>{inquiry.fallback_after_hours}h</strong> if there's no reply.
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ----------------------------- message ----------------------------- */

interface MessageProps {
  avatarColor: string;
  avatarInitials: string;
  senderName: string;
  senderHandle: string;
  recipientLabel: string;
  timestamp: string;
  body: string;
  direction: "in" | "out";
  highlight?: boolean;
  summary?: string | null;
  pdfUrl?: string | null;
  pdfFilename?: string | null;
  inboundAttachments?: import("../types").InquiryAttachment[] | null;
}

function Message(p: MessageProps) {
  return (
    <article className={`gm-msg ${p.highlight ? "gm-msg-highlight" : ""}`}>
      <header className="gm-msg-head">
        <div className="gm-avatar gm-avatar-msg" style={{ background: p.avatarColor }} aria-hidden>
          {p.avatarInitials}
        </div>
        <div className="gm-msg-meta">
          <div className="gm-msg-line">
            <span className="gm-msg-sender">{p.senderName}</span>
            <span className="gm-msg-handle">&lt;{p.senderHandle}&gt;</span>
          </div>
          <div className="gm-msg-recip">{p.recipientLabel}</div>
        </div>
        <div className="gm-msg-time">{p.timestamp}</div>
      </header>
      <div className="gm-msg-body">{p.body}</div>

      {(() => {
        const atts = p.inboundAttachments?.length
          ? p.inboundAttachments
          : p.pdfUrl
          ? [{ id: 0, url: p.pdfUrl, filename: p.pdfFilename, summary: p.summary }]
          : [];
        return atts.map((att, i) => (
          <div key={att.id > 0 ? att.id : `att-${i}`}>
            {att.summary && (
              <div className="gm-msg-section">
                <div className="gm-msg-section-label">
                  {atts.length > 1 && att.filename ? `Attachment Summary — ${att.filename}` : "Attachment Summary"}
                </div>
                <div className="gm-msg-section-body">{att.summary}</div>
              </div>
            )}
            {att.url && (
              <a href={att.url} target="_blank" rel="noreferrer" className="gm-msg-pdf-link">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <path d="M14 2v6h6" />
                </svg>
                <span>{att.filename || "Open attachment"}</span>
              </a>
            )}
          </div>
        ));
      })()}
    </article>
  );
}
