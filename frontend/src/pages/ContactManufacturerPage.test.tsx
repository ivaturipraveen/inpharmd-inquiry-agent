import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockList = vi.fn();
const mockManufacturersList = vi.fn();
const mockInquiriesList = vi.fn();

vi.mock("../api", () => ({
  api: {
    manufacturers: { list: (...args: any[]) => mockManufacturersList(...args) },
    inquiries: { list: (...args: any[]) => mockInquiriesList(...args) },
    externalInquiries: { list: (...args: any[]) => mockList(...args) },
  },
}));

// Stub unrelated form/dispatch components so tests focus on
// ContactManufacturerPage's own hydration logic.
vi.mock("../components/InquiryForm", () => ({ default: () => null }));
vi.mock("../components/ChannelChooser", () => ({ default: () => null }));
vi.mock("../components/ManufacturerForm", () => ({ default: () => null }));
vi.mock("../components/StatusBadge", () => ({ default: () => null }));
vi.mock("../components/InquiryDetail", () => ({ default: () => null }));

import ContactManufacturerPage from "./ContactManufacturerPage";

const setHash = (hash: string) => {
  window.location.hash = hash;
};

describe("ContactManufacturerPage — deep-link hydration", () => {
  beforeEach(() => {
    mockList.mockReset();
    mockManufacturersList.mockReset().mockResolvedValue([]);
    mockInquiriesList.mockReset().mockResolvedValue([]);
    sessionStorage.clear();
    setHash("#contact-manufacturer");
  });

  afterEach(() => {
    setHash("");
  });

  it("hydrates title/team/type/attachments and converges to the canonical full-context URL", async () => {
    mockList.mockResolvedValue({
      data: {
        data: [
          {
            inquiry_uuid: "abc-123",
            title: "Stability question for Drug X",
            inquiry_submitter: "Jane Pharmacist",
            inquiry_submitter_details: { first_name: "Jane", last_name: "Pharmacist", team_name: "Oncology Team" },
            temperature_excursion: true,
            attachments: [{ id: 1, file_name: "sheet.xlsx", doc_url: "https://x/sheet.xlsx" }],
            mue_details: "Excursion details here",
          },
        ],
        meta: { page: 1, per_page: 20, total_entries: 1, total_pages: 1 },
      },
      meta: { cache: "MISS", cacheAgeSeconds: null, upstreamError: null },
    });

    setHash("#contact-manufacturer?uuid=abc-123");
    render(<ContactManufacturerPage />);

    await waitFor(() =>
      expect(mockList).toHaveBeenCalledWith({ search: "abc-123" }),
    );
    expect(await screen.findByText("Stability question for Drug X")).toBeInTheDocument();
    expect(screen.getByText("Jane Pharmacist")).toBeInTheDocument();
    expect(screen.getByText("Temperature Excursion")).toBeInTheDocument();

    // Must converge onto the same canonical shape startContactManufacturerFlow
    // produces — not stay on the bare uuid-only hash.
    await waitFor(() => {
      expect(window.location.hash).toContain("uuid=abc-123");
      expect(window.location.hash).toContain("title=Stability+question+for+Drug+X");
      expect(window.location.hash).toContain("submitter=Jane+Pharmacist");
      expect(window.location.hash).toContain("type=Temperature+Excursion");
      expect(window.location.hash).toContain("team_name=Oncology+Team");
      expect(window.location.hash).toContain("mue_details=Excursion+details+here");
      expect(window.location.hash).toContain("att_url_0=");
    });
  });

  it("retains type and submitter across a refresh (sessionStorage cleared, URL only)", async () => {
    mockList.mockResolvedValue({
      data: {
        data: [
          {
            inquiry_uuid: "abc-123",
            title: "Stability question for Drug X",
            inquiry_submitter_details: { first_name: "Jane", last_name: "Pharmacist", team_name: "Oncology Team" },
            temperature_excursion: true,
            attachments: [],
            mue_details: "",
          },
        ],
        meta: { page: 1, per_page: 20, total_entries: 1, total_pages: 1 },
      },
      meta: { cache: "MISS", cacheAgeSeconds: null, upstreamError: null },
    });

    setHash("#contact-manufacturer?uuid=abc-123");
    render(<ContactManufacturerPage />);
    await waitFor(() => expect(window.location.hash).toContain("type=Temperature+Excursion"));
    cleanup();

    // Simulate a real refresh: sessionStorage gone, only the URL survives.
    sessionStorage.clear();
    mockList.mockClear();
    render(<ContactManufacturerPage />);

    expect(await screen.findByText("Temperature Excursion")).toBeInTheDocument();
    expect(screen.getByText("Jane Pharmacist")).toBeInTheDocument();
    expect(mockList).not.toHaveBeenCalled();
  });

  it("does not call the hydration endpoint when the deep link already carries full context", async () => {
    setHash(
      "#contact-manufacturer?uuid=abc-123&title=Already+known+title&team_name=Cardiology",
    );
    render(<ContactManufacturerPage />);

    expect(await screen.findByText("Already known title")).toBeInTheDocument();
    expect(mockList).not.toHaveBeenCalled();
  });

  it("shows an error banner (without crashing) when hydration fails", async () => {
    mockList.mockRejectedValue(new Error("Staging access token expired. Please log in again."));

    setHash("#contact-manufacturer?uuid=abc-123");
    render(<ContactManufacturerPage />);

    expect(
      await screen.findByText("Staging access token expired. Please log in again."),
    ).toBeInTheDocument();
    // Page still renders its normal shell instead of crashing.
    expect(screen.getByText("Contact Manufacturer")).toBeInTheDocument();
  });

  it("shows a clear error when the uuid is not found in the list (e.g. no longer open)", async () => {
    mockList.mockResolvedValue({
      data: { data: [], meta: { page: 1, per_page: 20, total_entries: 0, total_pages: 1 } },
      meta: { cache: "MISS", cacheAgeSeconds: null, upstreamError: null },
    });

    setHash("#contact-manufacturer?uuid=abc-123");
    render(<ContactManufacturerPage />);

    expect(
      await screen.findByText(/abc-123 was not found in InpharmD/),
    ).toBeInTheDocument();
    expect(screen.getByText("Contact Manufacturer")).toBeInTheDocument();
  });

  it("shows the empty state when there is no uuid at all", async () => {
    setHash("#contact-manufacturer");
    render(<ContactManufacturerPage />);

    expect(await screen.findByText(/No inquiry context/)).toBeInTheDocument();
    expect(mockList).not.toHaveBeenCalled();
    // Let the page's own (unrelated) manufacturers-list fetch settle before
    // the test tears down, so it doesn't warn about an act() update after exit.
    await waitFor(() => expect(mockManufacturersList).toHaveBeenCalled());
  });
});
