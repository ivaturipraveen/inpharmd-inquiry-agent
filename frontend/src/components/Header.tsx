import { FC } from "react";

export type TabKey = "manufacturers" | "inquiries";

interface Props {
  active: TabKey;
  onChange: (tab: TabKey) => void;
}

const TABS: { key: TabKey; label: string }[] = [
  { key: "manufacturers", label: "Manufacturers" },
  { key: "inquiries", label: "Inquiries" },
];

const Header: FC<Props> = ({ active, onChange }) => (
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
      </nav>
    </div>
  </header>
);

export default Header;
