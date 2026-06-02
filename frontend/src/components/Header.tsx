import { FC } from "react";

export type TabKey = "manufacturers" | "inquiries" | "emails";

interface Props {
  active: TabKey;
  onChange: (tab: TabKey) => void;
  onLogout?: () => void;
}

const TABS: { key: TabKey; label: string }[] = [
  { key: "manufacturers", label: "Manufacturers" },
  { key: "inquiries", label: "Manufacturer Outreach" },
  { key: "emails", label: "Emails" },
];

const Header: FC<Props> = ({ active, onChange, onLogout }) => (
  <header className="site-header">
    <div className="site-header-inner">
      <div className="brand">
        <img src="/logo.png" alt="InpharmD" className="brand-logo" />
        <span className="brand-sub">Manufacturer MI Directory</span>
      </div>
      <nav className="tab-nav" aria-label="Sections">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            className={`tab ${active === t.key ? "tab-active" : ""}`}
            onClick={() => onChange(t.key)}
          >
            {t.label}
          </button>
        ))}
        {onLogout && (
          <button
            type="button"
            className="tab tab-logout"
            onClick={onLogout}
            title="Sign out"
          >
            Sign out
          </button>
        )}
      </nav>
    </div>
  </header>
);

export default Header;
