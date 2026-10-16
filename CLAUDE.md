# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Backend

```bash
cd backend
source .venv/bin/activate          # activate existing venv (Python 3.13 in .venv)
pip install -r requirements.txt    # install deps
cp .env.example .env               # first-time setup — fill in DATABASE_URL + service keys
python seed.py                     # one-time: import Manufacturer_MI_Contact_Database.xlsx into DB
uvicorn main:app --reload --port 8000
```

API docs at `http://localhost:8000/docs`.

Run a single test:
```bash
cd backend && source .venv/bin/activate
python -m pytest tests/test_excel_service.py -v
```

### Frontend

```bash
cd frontend
npm install
# create frontend/.env with: VITE_API_URL=http://localhost:8000
npm run dev     # dev server at http://localhost:5173 (proxies /api → port 8000)
npm run build   # TypeScript check + Vite bundle → dist/
```

## Architecture

### Data model

Three SQLAlchemy models in `backend/models.py`:

- **`ManufacturerContact`** — pharmaceutical manufacturer MI directory (90 US companies, seeded from Excel)
- **`Inquiry`** — a clinical question sent to a manufacturer; rich lifecycle (see below)
- **`User`** — local mirror of an InpharmD platform user; stores a `session_token` sent to the browser and a `staging_token` kept server-side for proxying

**Inquiry lifecycle states:** `draft → email_sent → email_responded → call_pending → call_completed → closed`. Also: `needs_attention` (retries exhausted), `failed` (manual).

The DB schema grows via `_ensure_columns()` in `main.py` (inline `ALTER TABLE … ADD COLUMN IF NOT EXISTS` statements) rather than a migration tool — add new columns there.

### Backend (`backend/`)

FastAPI app with these routers:

| Router | Prefix | Purpose |
|---|---|---|
| `manufacturers.py` | `/api/manufacturers` | CRUD for ManufacturerContact (public — no auth) |
| `inquiries.py` | `/api/inquiries` | Full inquiry lifecycle; bulk create; voice/email dispatch |
| `auth.py` | `/api/auth` | Login/logout/me — proxies to staging InpharmD, mints local `session_token` |
| `external_inquiries.py` | `/api/external/inquiries` | Pass-through proxy to staging; returns cache metadata headers |
| `webhooks.py` | `/api/webhooks/elevenlabs/post-call` | ElevenLabs post-call payload → writes result back to inquiry |
| `agent_tools.py` | `/api/agent-tools/submit-answer` | Called by ElevenLabs agent mid-call to record a structured answer |
| `email_inbound.py` | `/api/email/inbound/{token}` | Inbound email parsing (SendGrid); `token` must match `EMAIL_INBOUND_SECRET` — no other auth is possible since Inbound Parse can't send a signature |

**Auth pattern:** All inquiry/external endpoints require `X-Session-Token` header (FastAPI dep `get_current_user` in `auth.py`). The `session_token` maps to a `User` row; the `staging_token` (InpharmD access token) never leaves the server.

**Services (called by routers):**

- `email_service.py` — SendGrid outbound email with `[InpharmD #N]` subject tag
- `call_service.py` — ElevenLabs outbound voice calls (async + sync variants)
- `graph_service.py` — Microsoft Graph API; polls `druginfo@inpharmd.com` for manufacturer replies; subject-tag match → writes back to inquiry
- `inpharmd_service.py` — HTTP client for staging InpharmD API (login, list MUE inquiries, submitter details); retry logic for Heroku cold starts
- `legacy_response_service.py` — POSTs final answers back to the staging `/api/legacy/manufacturing_response` endpoint when `source_inquiry_uuid` is set
- `excel_service.py` — downloads `.xlsx/.xls/.csv` from S3/signed URLs; extracts Manufacturer column; fuzzy-matches names to DB
- `excel_writeback_service.py` — patches the "Manufacturer Response" column in the MUE Excel and re-uploads to S3
- `s3_service.py` — uploads PDFs and Excel files to S3/R2
- `summary_service.py` — OpenAI GPT: extracts clean answers from call transcripts and PDF text
- `cache_service.py` — in-process TTL cache for staging API responses (HIT/MISS/STALE)
- `slack_service.py` — posts answer cards to Slack on email reply or call completion
- `scheduler.py` — APScheduler background jobs: auto-retry voicemail/no-answer calls every 60s; IMAP email poll every 60s

### Frontend (`frontend/src/`)

Single-page React + TypeScript app. Navigation is hash-based (`#manufacturers`, `#inquiries`, etc.).

**Pages → tabs:**

| File | Tab key | Description |
|---|---|---|
| `ManufacturersPage.tsx` | `manufacturers` | Searchable table of manufacturer contacts |
| `InquiriesPage.tsx` | `inquiries` | Outreach tab — user's own inquiries with full lifecycle actions |
| `ExternalInquiriesPage.tsx` | `platform-inquiries` | MUE inquiries pulled from staging InpharmD (with cache badge) |
| `ContactManufacturerPage.tsx` | `contact-manufacturer` | Bulk dispatch: upload MUE Excel → auto-match manufacturers → email/call/draft |
| `EmailsPage.tsx` | `emails` | Email thread viewer |
| `LoginPage.tsx` | — | Proxies auth to staging InpharmD |

**Key cross-cutting files:**
- `api.ts` — all API calls; `session` helper reads/writes `localStorage`; 401 fires `inpharmd:session-expired` event
- `types.ts` — shared TypeScript interfaces (`ManufacturerContact`, `Inquiry`, `InquiryStatus`, etc.)
- `App.tsx` — session restoration on load; tab routing; logout

**Vite proxy:** In dev, `/api/*` is proxied to `VITE_API_URL` (default `http://localhost:8000`), so the frontend never needs CORS headers in dev.

### Outbound communication channels

An inquiry can be dispatched three ways (set at create time via `dispatch_channel`):

1. **email** — SendGrid → manufacturer MI email → IMAP/Graph reply polling → writes `email_response` + `final_answer`
2. **call** — ElevenLabs outbound voice call → post-call webhook → writes `call_transcript` / `call_summary` / `final_answer`; auto-retries on voicemail/no_answer
3. **none** — draft only; user dispatches manually from Outreach tab

Once `final_answer` is set, `legacy_response_service` POSTs it to staging InpharmD (if `source_inquiry_uuid` present) and `slack_service` sends a card to the configured channel.

## Environment variables (backend)

| Key | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL (Render Postgres External URL) |
| `CORS_ORIGINS` | Comma-separated allowed frontend origins |
| `ELEVENLABS_API_KEY` | ElevenLabs API key |
| `ELEVENLABS_INQUIRY_AGENT_ID` | Agent ID for outbound calls |
| `ELEVENLABS_INQUIRY_PHONE_NUMBER_ID` | `phn_…` ID from ElevenLabs dashboard |
| `ELEVENLABS_WEBHOOK_SECRET` | Shared secret for post-call webhook |
| `AGENT_TOOLS_SECRET` | Shared secret for `submit_answer` tool |
| `SENDGRID_API_KEY` | Outbound email |
| `EMAIL_FROM` | Sender address (default: `druginfo@inpharmd.com`) |
| `EMAIL_INBOUND_SECRET` | Path-token secret for the SendGrid Inbound Parse webhook (`/api/email/inbound/{token}`) — required, fails closed if unset |
| `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` | Microsoft Graph mailbox polling |
| `OPENAI_API_KEY` | GPT for transcript/PDF answer extraction |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_S3_BUCKET` | S3/R2 PDF + Excel storage |
| `SLACK_BOT_TOKEN` / `SLACK_CHANNEL_ID` | Slack answer cards |
| `INPHARMD_API_BASE_URL` | **Required, no fallback** — must point at the InpharmD platform instance for this environment. For staging, set to `https://staging-mercer-inpharmd.herokuapp.com`. The app raises `InpharmdConfigError` and refuses the request if unset or malformed — this is intentional (a prior incident let an unset/misspelled value silently route production-bound data into staging; see `inpharmd_service.py`'s docstring). |

Generate webhook/tool secrets with `openssl rand -hex 32`.
