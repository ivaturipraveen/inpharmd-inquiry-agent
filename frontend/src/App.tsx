import { useEffect, useState } from "react";
import Header, { TabKey } from "./components/Header";
import ManufacturersPage from "./pages/ManufacturersPage";
import InquiriesPage from "./pages/InquiriesPage";

const isTab = (v: string): v is TabKey =>
  v === "manufacturers" || v === "inquiries";

const readHash = (): TabKey => {
  const h = window.location.hash.replace("#", "");
  return isTab(h) ? h : "manufacturers";
};

export default function App() {
  const [tab, setTab] = useState<TabKey>(readHash());

  useEffect(() => {
    const onHash = () => setTab(readHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const changeTab = (next: TabKey) => {
    window.location.hash = next;
    setTab(next);
  };

  return (
    <>
      <Header active={tab} onChange={changeTab} />
      <main className="page">
        {tab === "manufacturers" ? <ManufacturersPage /> : <InquiriesPage />}
      </main>
    </>
  );
}
