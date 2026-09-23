import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const sessionState = { token: null as string | null };

vi.mock("./api", () => ({
  api: {
    auth: {
      me: () => Promise.reject(new Error("not used in this test")),
      logout: () => Promise.resolve({ ok: true }),
    },
  },
  session: {
    get: () => sessionState.token,
    set: (t: string) => { sessionState.token = t; },
    clear: () => { sessionState.token = null; },
  },
}));

vi.mock("./pages/ManufacturersPage", () => ({ default: () => <div>ManufacturersPage</div> }));
vi.mock("./pages/InquiriesPage", () => ({ default: () => <div>InquiriesPage</div> }));
vi.mock("./pages/EmailsPage", () => ({ default: () => <div>EmailsPage</div> }));
vi.mock("./pages/ExternalInquiriesPage", () => ({ default: () => <div>ExternalInquiriesPage</div> }));
vi.mock("./pages/ContactManufacturerPage", () => ({
  default: () => <div>ContactManufacturerPage — hash: {window.location.hash}</div>,
}));
vi.mock("./pages/LoginPage", () => ({
  default: ({ onLogin }: { onLogin: (u: any) => void }) => (
    <button onClick={() => onLogin({ id: 1, email: "user@example.com" })}>
      Fake Sign In
    </button>
  ),
}));

import App from "./App";

describe("App — deep-link uuid survives the login flow", () => {
  beforeEach(() => {
    sessionState.token = null;
    localStorage.clear();
    window.location.hash = "#contact-manufacturer?uuid=abc-123";
  });

  it("shows the login screen (not the tab) while logged out, without losing the hash", () => {
    render(<App />);
    expect(screen.getByText("Fake Sign In")).toBeInTheDocument();
    expect(window.location.hash).toBe("#contact-manufacturer?uuid=abc-123");
  });

  it("renders Contact Manufacturer with the same uuid once the user logs in", () => {
    render(<App />);
    fireEvent.click(screen.getByText("Fake Sign In"));

    expect(
      screen.getByText("ContactManufacturerPage — hash: #contact-manufacturer?uuid=abc-123"),
    ).toBeInTheDocument();
    expect(window.location.hash).toBe("#contact-manufacturer?uuid=abc-123");
  });
});
