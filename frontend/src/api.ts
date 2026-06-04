import type {
  Inquiry,
  InquiryInput,
  ManufacturerContact,
  ManufacturerContactInput,
} from "./types";

const BASE = import.meta.env.VITE_API_URL || "";
const SESSION_TOKEN_KEY = "inpharmd_session_token";

export const session = {
  get: (): string | null => localStorage.getItem(SESSION_TOKEN_KEY),
  set: (token: string) => localStorage.setItem(SESSION_TOKEN_KEY, token),
  clear: () => localStorage.removeItem(SESSION_TOKEN_KEY),
};

export class UnauthorizedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "UnauthorizedError";
  }
}

// When any API call returns 401 we clear the session AND broadcast an
// event so the top-level App can drop the cached user object too. Without
// this, App.tsx keeps showing the authenticated UI even though every
// subsequent API call will 401.
const handleUnauthorized = (text: string) => {
  session.clear();
  try {
    window.dispatchEvent(new Event("inpharmd:session-expired"));
  } catch {
    /* SSR / non-browser env */
  }
  return new UnauthorizedError(text || "Session expired. Please sign in again.");
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  const token = session.get();
  if (token) headers["X-Session-Token"] = token;

  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    const text = await res.text();
    if (res.status === 401) throw handleUnauthorized(text);
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// Variant of `request` that also returns selected response headers — used
// by the external-inquiries page so it can show "Loaded from cache N min ago"
// and the underlying upstream error when we serve stale data.
export interface ApiMeta {
  cache: "HIT" | "MISS" | "STALE" | null;
  cacheAgeSeconds: number | null;
  upstreamError: string | null;
}

async function requestWithMeta<T>(
  path: string,
  init?: RequestInit,
): Promise<{ data: T; meta: ApiMeta }> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  const token = session.get();
  if (token) headers["X-Session-Token"] = token;

  const started = performance.now();
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  const ms = Math.round(performance.now() - started);

  if (!res.ok) {
    const text = await res.text();
    console.warn(`[api] ${init?.method || "GET"} ${path} → ${res.status} (${ms}ms)`, text);
    if (res.status === 401) throw handleUnauthorized(text);
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  const meta: ApiMeta = {
    cache: (res.headers.get("X-Cache") as ApiMeta["cache"]) || null,
    cacheAgeSeconds: res.headers.get("X-Cache-Age")
      ? Number(res.headers.get("X-Cache-Age"))
      : null,
    upstreamError: res.headers.get("X-Upstream-Error"),
  };
  // Visible browser-side log — group by cache state so you can scan it fast.
  const tag =
    meta.cache === "HIT"
      ? "%c[api] %s → 200 (cache HIT, age %ds, %dms)"
      : meta.cache === "STALE"
      ? "%c[api] %s → 200 (cache STALE, age %ds, %dms) upstream: %s"
      : meta.cache === "MISS"
      ? "%c[api] %s → 200 (cache MISS, fetched fresh, %dms)"
      : "%c[api] %s → 200 (%dms)";
  const colour =
    meta.cache === "HIT"
      ? "color: #6366f1; font-weight: 600"
      : meta.cache === "STALE"
      ? "color: #b91c1c; font-weight: 600"
      : meta.cache === "MISS"
      ? "color: #10b981; font-weight: 600"
      : "color: #64748b";
  if (meta.cache === "STALE") {
    console.log(tag, colour, path, meta.cacheAgeSeconds ?? 0, ms, meta.upstreamError);
  } else if (meta.cache === "HIT") {
    console.log(tag, colour, path, meta.cacheAgeSeconds ?? 0, ms);
  } else {
    console.log(tag, colour, path, ms);
  }

  const data = res.status === 204 ? (undefined as T) : ((await res.json()) as T);
  return { data, meta };
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
    testCall: (id: number, phoneNumber: string) =>
      request<Inquiry>(`/api/inquiries/${id}/test-call`, {
        method: "POST",
        body: JSON.stringify({ phone_number: phoneNumber }),
      }),
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
    extractAnswer: (id: number) =>
      request<Inquiry>(`/api/inquiries/${id}/extract-answer`, { method: "POST" }),
  },
  voice: {
    signedUrl: () =>
      request<{ signed_url: string; agent_id: string }>(`/api/voice/signed-url`),
  },
  auth: {
    login: (email: string, password: string, channel_id?: string) =>
      request<{
        session_token: string;
        user: {
          id: number;
          email: string;
          display_name?: string | null;
          staging_user_id?: string | null;
          last_login_at?: string | null;
        };
      }>(`/api/auth/login`, {
        method: "POST",
        body: JSON.stringify({ email, password, channel_id }),
      }),
    me: () =>
      request<{
        user: {
          id: number;
          email: string;
          display_name?: string | null;
          staging_user_id?: string | null;
          last_login_at?: string | null;
        };
      }>(`/api/auth/me`),
    logout: () => request<{ ok: boolean }>(`/api/auth/logout`, { method: "POST" }),
  },
  externalInquiries: {
    // Pass-through from staging — we keep it loosely typed because the
    // upstream schema isn't fully nailed down on our side yet.
    // Returns the data PLUS cache metadata so the UI can show
    // "Loaded from cache N min ago" / upstream error info.
    list: (params?: {
      status?: string;
      page?: number;
      per_page?: number;
      fresh?: boolean;
    }) => {
      const q = new URLSearchParams();
      if (params?.status) q.set("status", params.status);
      if (params?.page) q.set("page", String(params.page));
      if (params?.per_page) q.set("per_page", String(params.per_page));
      if (params?.fresh) q.set("fresh", "true");
      const suffix = q.toString() ? `?${q}` : "";
      return requestWithMeta<any>(`/api/external/inquiries${suffix}`);
    },
    get: (id: string | number, fresh = false) => {
      const suffix = fresh ? "?fresh=true" : "";
      return requestWithMeta<any>(
        `/api/external/inquiries/${encodeURIComponent(String(id))}${suffix}`,
      );
    },
  },
};
