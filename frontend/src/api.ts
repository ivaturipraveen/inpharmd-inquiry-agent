import type {
  Inquiry,
  InquiryInput,
  ManufacturerContact,
  ManufacturerContactInput,
} from "./types";

const BASE = import.meta.env.VITE_API_URL || "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  manufacturers: {
    list: (q?: string) =>
      request<ManufacturerContact[]>(
        `/api/manufacturers${q ? `?q=${encodeURIComponent(q)}` : ""}`
      ),
    create: (data: ManufacturerContactInput) =>
      request<ManufacturerContact>(`/api/manufacturers`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
    update: (id: number, data: Partial<ManufacturerContactInput>) =>
      request<ManufacturerContact>(`/api/manufacturers/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
      }),
    remove: (id: number) =>
      request<void>(`/api/manufacturers/${id}`, { method: "DELETE" }),
  },
  inquiries: {
    list: (params?: { status?: string; manufacturer_id?: number }) => {
      const q = new URLSearchParams();
      if (params?.status) q.set("status", params.status);
      if (params?.manufacturer_id)
        q.set("manufacturer_id", String(params.manufacturer_id));
      const suffix = q.toString() ? `?${q}` : "";
      return request<Inquiry[]>(`/api/inquiries${suffix}`);
    },
    get: (id: number) => request<Inquiry>(`/api/inquiries/${id}`),
    create: (data: InquiryInput) =>
      request<Inquiry>(`/api/inquiries`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
    update: (id: number, data: Partial<InquiryInput> & { status?: string }) =>
      request<Inquiry>(`/api/inquiries/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
      }),
    remove: (id: number) =>
      request<void>(`/api/inquiries/${id}`, { method: "DELETE" }),
    sendEmail: (id: number) =>
      request<Inquiry>(`/api/inquiries/${id}/send-email`, { method: "POST" }),
    recordEmailResponse: (id: number, response: string) =>
      request<Inquiry>(`/api/inquiries/${id}/record-email-response`, {
        method: "POST",
        body: JSON.stringify({ response }),
      }),
    triggerCall: (id: number, force = false) =>
      request<Inquiry>(
        `/api/inquiries/${id}/trigger-call${force ? "?force=true" : ""}`,
        { method: "POST" }
      ),
    businessHours: (id: number) =>
      request<{
        known: boolean;
        in_hours?: boolean;
        hours_text?: string | null;
        phone?: string | null;
      }>(`/api/inquiries/${id}/business-hours`, { method: "POST" }),
    recordCallResult: (id: number, summary: string, transcript?: string) =>
      request<Inquiry>(`/api/inquiries/${id}/record-call-result`, {
        method: "POST",
        body: JSON.stringify({ summary, transcript: transcript ?? null }),
      }),
    close: (id: number) =>
      request<Inquiry>(`/api/inquiries/${id}/close`, { method: "POST" }),
  },
};
