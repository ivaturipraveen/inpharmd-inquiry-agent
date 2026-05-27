# InpharmD — Manufacturer MI Directory & Voice Inquiry Agent

Internal app for pharmacy operations:

1. **Manufacturer MI Directory** — searchable, filterable database of pharmaceutical manufacturers' Medical Information channels (90 US companies seeded from `Manufacturer_MI_Contact_Database (1).xlsx`).
2. **Inquiry workflow** — submit a clinical question against any manufacturer; choose either to send it by email or fire an ElevenLabs voice agent that calls the manufacturer's MI line on your behalf, captures the answer, and writes it back to the inquiry record.

## Layout

```
backend/       FastAPI + SQLAlchemy + psycopg3 (PostgreSQL on Render)
frontend/      Vite + React + TypeScript (deploys as a Render Static Site)
render.yaml    Blueprint that provisions both services in one click
ELEVENLABS_INQUIRY_AGENT_SETUP.md   Step-by-step agent setup guide
```

## Local development

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env       # fill in DATABASE_URL + ElevenLabs vars
python seed.py             # one-time: import the Excel into the DB

uvicorn main:app --reload --port 8000
```

API docs at `http://localhost:8000/docs`.

### Frontend

```bash
cd frontend
npm install
cp .env.example .env       # set VITE_API_URL=http://localhost:8000
npm run dev
```

Open `http://localhost:5173`.

## Deploy to Render

This repo includes a `render.yaml` blueprint that provisions:
- **`inpharmd-inquiry-api`** — FastAPI backend (Web Service, `backend/`)
- **`inpharmd-inquiry-web`** — React SPA (Static Site, `frontend/`)

### One-time setup

1. Push this repo to GitHub
2. In the Render dashboard → **New → Blueprint** → connect the GitHub repo
3. Render reads `render.yaml` and creates both services
4. Open each service and fill in the secrets (marked `sync: false` in the blueprint):
   - **API service** → `DATABASE_URL` (your existing Render Postgres External URL), plus the five `ELEVENLABS_*` / `AGENT_TOOLS_SECRET` values
   - Frontend has no secrets — `VITE_API_URL` is baked in at build time
5. Trigger the first deploy. Within ~5 minutes both URLs will be live:
   - API: `https://inpharmd-inquiry-api.onrender.com`
   - Web: `https://inpharmd-inquiry-web.onrender.com`

### Updating

Every push to `main` auto-deploys both services. To change env vars, edit them in the Render dashboard (no redeploy needed for most vars; restart the service to pick them up).

## Voice agent setup

After the backend is deployed, follow `ELEVENLABS_INQUIRY_AGENT_SETUP.md` to:
- Link a Twilio number to ElevenLabs
- Create the `InpharmD MI Inquiry` conversational agent
- Configure the `submit_answer` tool against `https://inpharmd-inquiry-api.onrender.com/api/agent-tools/submit-answer`
- Configure the post-call webhook against `https://inpharmd-inquiry-api.onrender.com/api/webhooks/elevenlabs/post-call`

## Environment variables (backend)

| Key | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (Render Postgres External URL) |
| `CORS_ORIGINS` | Comma-separated list of allowed frontend origins |
| `ELEVENLABS_API_KEY` | ElevenLabs API key — places outbound calls |
| `ELEVENLABS_INQUIRY_AGENT_ID` | Agent ID created in the ElevenLabs dashboard |
| `ELEVENLABS_INQUIRY_PHONE_NUMBER_ID` | `phn_…` ID from Conversational AI → Phone Numbers |
| `ELEVENLABS_WEBHOOK_SECRET` | Shared secret for the post-call webhook (any random value) |
| `AGENT_TOOLS_SECRET` | Shared secret the agent sends in `X-Agent-Secret` for `submit_answer` |

Generate the two secrets with `openssl rand -hex 32`.
