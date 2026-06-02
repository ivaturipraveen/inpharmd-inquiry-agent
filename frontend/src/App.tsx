import { useEffect, useState } from "react";
import Header, { TabKey } from "./components/Header";
import ManufacturersPage from "./pages/ManufacturersPage";
import InquiriesPage from "./pages/InquiriesPage";
import EmailsPage from "./pages/EmailsPage";
import LoginPage from "./pages/LoginPage";

const AUTH_KEY = "inpharmd_auth";

interface AuthUser {
  email: string;
}

const isTab = (v: string): v is TabKey =>
  v === "manufacturers" || v === "inquiries" || v === "emails";

const readHash = (): TabKey => {
  // hash can be "#inquiries" or "#inquiries?id=42" — extract just the tab part.
  const h = window.location.hash.replace(/^#/, "").split("?")[0];
  return isTab(h) ? h : "manufacturers";
};

const readAuth = (): AuthUser | null => {
  try {
    const raw = localStorage.getItem(AUTH_KEY);
    return raw ? (JSON.parse(raw) as AuthUser) : null;
  } catch {
    return null;
  }
};

export default function App() {
  const [tab, setTab] = useState<TabKey>(readHash());
  const [user, setUser] = useState<AuthUser | null>(readAuth());

  useEffect(() => {
    const onHash = () => setTab(readHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const changeTab = (next: TabKey) => {
    window.location.hash = next;
    setTab(next);
  };

  const handleLogin = (u: AuthUser) => {
    localStorage.setItem(AUTH_KEY, JSON.stringify(u));
    setUser(u);
  };

  const handleLogout = () => {
    localStorage.removeItem(AUTH_KEY);
    setUser(null);
  };

  if (!user) {
    return <LoginPage onLogin={handleLogin} />;
  }

  return (
    <>
      <Header active={tab} onChange={changeTab} onLogout={handleLogout} />
      <main className="page">
        {tab === "manufacturers" && <ManufacturersPage />}
        {tab === "inquiries" && <InquiriesPage />}
        {tab === "emails" && <EmailsPage />}
      </main>
    </>
  );
}
