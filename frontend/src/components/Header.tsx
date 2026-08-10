import { FC } from "react";

export type TabKey =
  | "manufacturers"
  | "inquiries"
  | "platform-inquiries"
  | "emails"
  // Sub-route of "platform-inquiries" — opens when the user clicks
  // "Contact manufacturer" from a row. Not shown in the nav.
  | "contact-manufacturer";

interface Props {
  active: TabKey;
  onChange: (tab: TabKey) => void;
  onLogout?: () => void;
}

const TABS: { key: TabKey; label: string }[] = [
  { key: "manufacturers", label: "Manufacturers" },
  { key: "inquiries", label: "Manufacturer Outreach" },
  { key: "platform-inquiries", label: "InpharmD Inquiries" },
  { key: "emails", label: "Emails" },
];

const Header: FC<Props> = ({ active, onChange, onLogout }) => {
  // The Contact-manufacturer page is launched from the InpharmD Inquiries
  // tab — visually it still belongs there, so keep that tab highlighted.
  const visualActive: TabKey =
    active === "contact-manufacturer" ? "platform-inquiries" : active;
  return (
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
            className={`tab ${visualActive === t.key ? "tab-active" : ""}`}
            onClick={() => onChange(t.key)}
          >
            {t.label}
          </button>
        ))}
        {onLogout && (
          <button
            type="button"
            className="tab tab-logout"
            onClick={() => { if (confirm("Sign out of InpharmD?")) onLogout(); }}
            title="Sign out"
          >
            Sign out
          </button>
        )}
      </nav>
    </div>
  </header>
  );
};

export default Header;
