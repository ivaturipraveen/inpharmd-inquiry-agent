// Shared, trivial UI constants — not environment config, just values
// previously duplicated as literals across multiple files.

export const TOAST_DURATION_MS = 6000;

// Mirrors the backend's DEFAULT_REQUESTER_NAME/EMAIL default identity
// (routers/inquiries.py) — kept in sync manually, not fetched at runtime.
export const DEFAULT_REQUESTER_NAME = "Leah";
export const DEFAULT_REQUESTER_EMAIL = "druginfo@inpharmd.com";
