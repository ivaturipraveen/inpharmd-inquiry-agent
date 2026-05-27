# InpharmD — Manufacturer MI Directory + Voice Inquiry Agent

**Status:** Live on Render · all core flows working end-to-end · ready for internal testing

---

## What it does

A pharmacy operations tool that helps the team reach pharmaceutical manufacturers' Medical Information desks to answer clinical questions:

1. **Browse the directory** — searchable, filterable database of 90 US manufacturer MI channels (Pfizer, Merck, Lilly, etc.) imported from the source Excel file
2. **Submit an inquiry** — pick a manufacturer, type the clinical question, optionally add who's asking
3. **Choose how to reach them** — two channels offered in a clean modal:
   - **Send Email** — actually delivers the inquiry to the manufacturer's MI inbox via SMTP, tagged so future replies can be matched back
   - **Call Agent Now** — places an outbound voice call via ElevenLabs Conversational AI; the agent asks the question on behalf of the pharmacist, captures the rep's verbal answer, and writes it back to the inquiry record
4. **Get a clean answer** — the agent's structured answer (or an AI-extracted summary of the full transcript) lands at the top of the inquiry, with the transcript preserved beneath it
5. **Resilient retries** — if a call hits voicemail or goes unanswered, the system automatically retries twice (2-minute gap), then surfaces it as "Needs Attention" for manual follow-up

---

## Live URLs

| Service | URL |
|---|---|
| **Web app** | https://inpharmd-inquiry-web.onrender.com |
| **Backend API** | https://inpharmd-inquiry-api.onrender.com |
| **API docs (Swagger)** | https://inpharmd-inquiry-api.onrender.com/docs |
| **GitHub repo** | https://github.com/ivaturipraveen/inpharmd-inquiry-agent |

---

## Tech Stack

| Layer | Choice |
|---|---|
| Frontend | React 18 + TypeScript + Vite, no external state library |
| Backend | FastAPI + SQLAlchemy 2.0, psycopg3 driver |
| Database | PostgreSQL (Render-managed) |
| Voice Agent | ElevenLabs Conversational AI + Twilio (outbound calls) |
| Email | SMTP via Gmail (App Password auth) |
| AI Summary | OpenAI GPT-4o-mini (transcript → clean answer) |
| Background Jobs | APScheduler in-process (retry queue) |
| Hosting | Render — separate Web Service (FastAPI) + Static Site (React build) |
| Auth (now) | None — internal tool, all routes open |

---

## Architecture

```
                ┌──────────────────────────────────────────────┐
                │  React SPA  (inpharmd-inquiry-web)           │
                │  Manufacturers tab · Inquiries tab           │
                └──────────────────────┬───────────────────────┘
                                       │  REST/JSON
                                       ▼
                ┌──────────────────────────────────────────────┐
                │  FastAPI  (inpharmd-inquiry-api)             │
                │  ├── /api/manufacturers   (CRUD + search)    │
                │  ├── /api/inquiries        (CRUD + lifecycle)│
                │  ├── /api/agent-tools/*    (submit_answer)   │
                │  ├── /api/webhooks/*       (post-call)       │
                │  └── APScheduler tick  (auto-retry job)      │
                └────┬──────────┬──────────┬──────────┬────────┘
                     │          │          │          │
                     ▼          ▼          ▼          ▼
                ┌────────┐ ┌────────┐ ┌──────────┐ ┌──────┐
                │Postgres│ │  SMTP  │ │ElevenLabs│ │OpenAI│
                │ Render │ │ Gmail  │ │ + Twilio │ │GPT-4o│
                └────────┘ └────────┘ └──────────┘ └──────┘

         ┌────────────────────────────────────────────────────┐
         │  Voice Agent (ElevenLabs "InpharmD MI Inquiry")    │
         │  System prompt: pharmacist-liaison persona         │
         │  Tool: submit_answer  →  POSTs to our backend      │
         │  Webhook: post-call   →  POSTs transcript to us    │
         └────────────────────────────────────────────────────┘
```

---

## What was built

### 1. Data ingestion
- Read 90 manufacturer rows × 17 columns from `Manufacturer_MI_Contact_Database (1).xlsx` (Pfizer, Hospira, AbbVie, Roche, Novartis, …)
- Designed `manufacturer_contacts` schema with all source columns (preferred channel, MI email, web form URL, phone, HCP portal, SLA, hours, notes, etc.) plus timestamps
- One-shot seed script (`backend/seed.py`) imports the Excel into Postgres on first deploy
- Added one test row `Yanthraa (TEST)` (id 92) pointing at a controlled phone/email so live testing never touches a real US manufacturer

### 2. Backend (FastAPI)
Full REST API with these resources:

| Resource | Endpoints |
|---|---|
| Manufacturers | GET / POST / PUT / DELETE / search via `?q=` |
| Inquiries | CRUD + lifecycle: `/send-email`, `/trigger-call`, `/business-hours`, `/extract-answer`, `/reset-retries`, `/close`, `/record-email-response`, `/record-call-result` |
| Agent tools | `/agent-tools/submit-answer` (secured by shared `X-Agent-Secret` header) |
| Webhooks | `/webhooks/elevenlabs/post-call` (transcript + summary auto-attached by `conversation_id`) |

Business-hours guard parses strings like `Mon-Sat 9a-9p IST` and `Mon-Fri 8a-6p ET` to block out-of-hours calls (with a "force" override).

### 3. ElevenLabs Voice Agent
- Created a dedicated agent (`InpharmD MI Inquiry`) — separate from any other agents so transcripts/analytics stay clean
- Custom system prompt: faithful inquiry liaison, not a clinician; reads the question verbatim; captures the rep's answer; doesn't interpret medical content
- Single tool — `submit_answer` — that POSTs back to our backend with structured `{outcome, answer, rep_name, rep_reference, notes}` before the agent hangs up
- Per-call dynamic variables (`{{inquiry_id}}`, `{{manufacturer_name}}`, `{{inquiry_subject}}`, `{{inquiry_question}}`, `{{requester_name}}`, `{{requester_email}}`) sent in the outbound-call API payload
- First-message override per call so the rep hears `"Hello, this is the InpharmD medical information line calling regarding..."` immediately
- Post-call webhook delivers the full transcript as a backup

### 4. Frontend (React + TypeScript)
- InpharmD-branded UI (white + orange accent matching the company website)
- Two top-nav tabs: **Manufacturers** / **Inquiries**
- Manufacturer page: stats bar, search, channel filter, HCP-login filter, expandable rows with full details, kebab-menu CRUD
- Inquiries page: stats bar, status filter, table sorted newest-first, click row → detail modal
- New Inquiry form: manufacturer picker (shows email/phone/SLA hints when selected), subject, question, optional requester, fallback window with preset chips + custom hours/days input
- Channel Chooser modal after submit: side-by-side Email vs Call cards with live business-hours indicator
- Inquiry Detail modal: **Final Answer at the top** (prominent orange card), question, vertical timeline (created → email → response → call → close), retry status banners, action panels (log email response, log call result manually, extract answer with AI)

### 5. Auto-retry + Needs Attention
- After a call ends with `voicemail` or `no_answer`: auto-schedule a retry 2 minutes later
- Max 2 retries → if still no answer, inquiry flips to `needs_attention` with a red banner and a prominent **Retry Call Manually** button
- Scheduler runs in-process (APScheduler) every 60s; survives transient ElevenLabs / network failures (re-tries on the next tick)
- Manual retry from the UI cancels any pending auto-retry and starts fresh

### 6. Email channel
- SMTP send via Gmail App Password (`ivaturipraveen11@gmail.com` for testing; will move to a shared `inquiries@yanthraa.com` mailbox later)
- Subject tagged `[InpharmD #N]` so future IMAP polling can match replies back to the inquiry
- Reply-To routes to the requester's email if filled, else to the SMTP sender
- Manual paste UI for now (`Log email response` panel) — IMAP auto-polling deferred until volume justifies it

### 7. AI answer extraction (fallback)
- If the agent failed to call `submit_answer`, the full transcript is sent to GPT-4o-mini via the post-call webhook
- A clean 1–3 sentence clinical answer is extracted and shown in the orange "Final Answer" card automatically
- Manual "✨ Extract Answer with AI" button on inquiries that have a transcript but no clean answer

### 8. Deployment + CI
- Render Web Service (FastAPI) + Render Static Site (React) — both auto-deploy on every push to `main`
- All secrets in Render Environment tab (never in git): `DATABASE_URL`, `ELEVENLABS_*`, `SMTP_*`, `OPENAI_API_KEY`, `AGENT_TOOLS_SECRET`
- `.env` files for local dev (gitignored); `runtime.txt` pins Python 3.12
- Comprehensive setup guide (`ELEVENLABS_INQUIRY_AGENT_SETUP.md`) and test playbook (`TEST.md`) with 8 real-drug example queries

---

## End-to-end demo flow (≤ 2 minutes)

1. Open https://inpharmd-inquiry-web.onrender.com → Inquiries tab → **+ New Inquiry**
2. Manufacturer: **Yanthraa (TEST)** · Subject: any · Question: paste any of the 8 examples from `TEST.md`
3. Click **Create Inquiry** → Channel Chooser appears
4. **Send Email** path: email lands in `praveen@yanthraa.com` ~5s later, reply lands in `ivaturipraveen11@gmail.com`, paste it into the inquiry detail's "Log email response" panel
5. **Call Agent Now** path: phone (+91 9848639655) rings, agent says the InpharmD opener, reads the question, captures the verbal answer, hangs up
6. ~10s later refresh the inquiry — orange "Final Answer" card at the top shows the captured response, transcript collapsed beneath

---

## What's deferred (intentionally)

| Feature | Why deferred |
|---|---|
| **IMAP auto-polling for email replies** | Manual paste works fine at low volume; will revisit when daily inquiry count > 10 |
| **HMAC verification of ElevenLabs post-call webhook** | Today we match by `conversation_id` which is a reasonable safety net; full HMAC sig verify is a 1-hour task when needed |
| **User accounts / multi-tenant** | Internal tool, single team — auth not in scope yet |
| **Scheduled email-to-call fallback firing** | Today the user manually picks email or call; auto-promote-email-to-call after N hours is one APScheduler job away |
| **Shared `inquiries@yanthraa.com` mailbox** | Testing from personal Gmail; production should use a shared workspace mailbox |
| **Daily summary email** | Decided against — would just be noise at current volume |
| **Send Email → real send for arbitrary user** | Currently all email comes from one configured Gmail; per-user Gmail OAuth would be the production solution |

---

## What's working today (verified live)

✅ Postgres has 91 manufacturers (90 real + 1 test) — `GET /api/manufacturers` returns all
✅ Inquiry create/read/update/delete via UI and API
✅ Business-hours parser correctly identifies Yanthraa's `Mon-Sat 9a-9p IST` window
✅ Outbound voice call places successfully and reaches the test phone
✅ Voice agent `submit_answer` tool writes structured results back
✅ Post-call webhook receives full transcript and updates inquiry
✅ Auto-retry scheduler runs every 60s; verified failed-call → 2-min wait → re-dial flow
✅ SMTP email sends from Gmail → arrives at Yanthraa test address
✅ AI extraction generates clean clinical answers from raw transcripts
✅ Frontend deploys on every push; status badges, filters, retry banners all functional

---

## Repo layout

```
inpharmd-inquiry-agent/
├── README.md                              start here
├── PROJECT_STATUS.md                      this file
├── TEST.md                                end-to-end test playbook + 8 example queries
├── ELEVENLABS_INQUIRY_AGENT_SETUP.md      step-by-step agent setup guide
├── Manufacturer_MI_Contact_Database (1).xlsx   source data
│
├── backend/                               FastAPI app
│   ├── main.py                            app entry, lifespan, CORS
│   ├── database.py / models.py / schemas.py
│   ├── seed.py                            one-shot Excel → Postgres import
│   ├── call_service.py                    ElevenLabs outbound-call wrapper
│   ├── email_service.py                   SMTP send wrapper
│   ├── summary_service.py                 OpenAI transcript-to-answer extractor
│   ├── scheduler.py                       APScheduler retry job
│   ├── routers/
│   │   ├── manufacturers.py
│   │   ├── inquiries.py                   lifecycle + send-email + trigger-call + extract-answer
│   │   ├── agent_tools.py                 /submit-answer for the voice agent
│   │   └── webhooks.py                    /elevenlabs/post-call
│   ├── runtime.txt                        Python 3.12.7
│   └── requirements.txt
│
└── frontend/                              Vite + React + TypeScript
    ├── public/logo.png                    InpharmD logo
    └── src/
        ├── App.tsx                        hash-based router (Manufacturers / Inquiries)
        ├── api.ts / types.ts
        ├── components/                    Header, StatsBar, FilterBar, ManufacturerTable, ManufacturerForm, RowMenu, InquiryForm, InquiryDetail, ChannelChooser, StatusBadge
        ├── pages/                         ManufacturersPage, InquiriesPage
        └── styles/theme.css               white + orange InpharmD palette
```
