import { useEffect, useState } from "react";
import Header, { TabKey } from "./components/Header";
import ManufacturersPage from "./pages/ManufacturersPage";
import InquiriesPage from "./pages/InquiriesPage";
import EmailsPage from "./pages/EmailsPage";
import ExternalInquiriesPage from "./pages/ExternalInquiriesPage";
import ContactManufacturerPage from "./pages/ContactManufacturerPage";
import LoginPage, { AuthUser } from "./pages/LoginPage";
import { api, session } from "./api";

const AUTH_KEY = "inpharmd_auth";

const isTab = (v: string): v is TabKey =>
  v === "manufacturers" ||
  v === "inquiries" ||
  v === "emails" ||
  v === "platform-inquiries" ||
  v === "contact-manufacturer";

const readHash = (): TabKey => {
  // hash can be "#inquiries" or "#inquiries?id=42" — extract just the tab part.
  const h = window.location.hash.replace(/^#/, "").split("?")[0];
  return isTab(h) ? h : "manufacturers";
};

const readAuth = (): AuthUser | null => {
  // A cached user is only valid if we still have a session token to send
  // with API calls. Otherwise the user was "logged in" by the old fake
  // login flow (or the token was cleared by a 401) — force re-login.
  if (!session.get()) {
    localStorage.removeItem(AUTH_KEY);
    return null;
  }
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
  const [checking, setChecking] = useState<boolean>(() => !!session.get());

  useEffect(() => {
    const onHash = () => setTab(readHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // When any API call 401s, api.ts clears the session token and fires this
  // event. We drop the cached user so the app routes back to login
  // immediately instead of looping on more 401s.
  useEffect(() => {
    const onExpired = () => {
      localStorage.removeItem(AUTH_KEY);
      setUser(null);
    };
    window.addEventListener("inpharmd:session-expired", onExpired);
    return () => window.removeEventListener("inpharmd:session-expired", onExpired);
  }, []);

  // Validate the cached session token on app load — if the backend has
  // forgotten it (e.g. DB reset, token rotated, deploy with cleared db),
  // route back to login instead of showing a broken authenticated UI.
  useEffect(() => {
    const token = session.get();
    if (!token || !user) {
      setChecking(false);
      return;
    }
    let cancelled = false;
    api.auth
      .me()
      .then((res) => {
        if (cancelled) return;
        const fresh: AuthUser = {
          id: res.user.id,
          email: res.user.email,
          display_name: res.user.display_name,
        };
        setUser(fresh);
        localStorage.setItem(AUTH_KEY, JSON.stringify(fresh));
      })
      .catch(() => {
        if (cancelled) return;
        // 401 already cleared the session token in api.ts; clear the user too.
        localStorage.removeItem(AUTH_KEY);
        setUser(null);
      })
      .finally(() => {
        if (!cancelled) setChecking(false);
      });
    return () => {
      cancelled = true;
    };
    // Only run once on mount; subsequent re-renders shouldn't re-validate.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const changeTab = (next: TabKey) => {
    window.location.hash = next;
    setTab(next);
  };

  const handleLogin = (u: AuthUser) => {
    localStorage.setItem(AUTH_KEY, JSON.stringify(u));
    setUser(u);
  };

  const handleLogout = async () => {
    // Only call the backend if we actually still have a session — without
    // a token the endpoint 401s and we don't care, we're tearing it down.
    if (session.get()) {
      try {
        await api.auth.logout();
      } catch {
        /* ignore — local cleanup below is the important part */
      }
    }
    session.clear();
    localStorage.removeItem(AUTH_KEY);
    setUser(null);
  };

  if (checking) {
    return (
      <main className="auth-page">
        <div className="auth-card" style={{ textAlign: "center" }}>
          <div className="auth-sub">Restoring your session…</div>
        </div>
      </main>
    );
  }

  if (!user) {
    return <LoginPage onLogin={handleLogin} />;
  }

  return (
    <>
      <Header active={tab} onChange={changeTab} onLogout={handleLogout} />
      <main className="page">
        {tab === "manufacturers" && <ManufacturersPage />}
        {tab === "inquiries" && <InquiriesPage />}
        {tab === "platform-inquiries" && <ExternalInquiriesPage />}
        {tab === "contact-manufacturer" && <ContactManufacturerPage />}
        {tab === "emails" && <EmailsPage />}
      </main>
    </>
  );
}
