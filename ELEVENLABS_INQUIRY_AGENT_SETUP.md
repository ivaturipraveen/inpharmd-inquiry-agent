# ElevenLabs MI Inquiry Agent — Setup Guide

Complete guide to set up your ElevenLabs voice agent as a Medical Information liaison that calls a pharmaceutical manufacturer on behalf of a pharmacist, asks a specific clinical question, and writes the answer back to the InpharmD inquiry database.

> This is a **separate agent** from the Nova personal assistant in `ELEVENLABS_SETUP.md`. They share infrastructure (Twilio number, ElevenLabs account) but have completely different prompts, tools, and analytics. Use a fresh agent so transcripts and call logs don't mix.

---

## Prerequisites

1. ElevenLabs account with Conversational AI access (paid plan — outbound calling requires it)
2. Twilio account with at least one voice-capable phone number (re-use the one already linked to Nova if you have it)
3. Backend deployed to Render at `https://inpharmd-inquiry-api.onrender.com` (or reachable via `ngrok http 8000` for local dev)
4. At least one row in the `manufacturer_contacts` table with a valid E.164 phone number to test against — the repo already ships with `Yanthraa (TEST)` at id 92 for this purpose

---

## Step 1: Deployed URLs

Both services are deployed to Render:


| Service         | URL                                         |
| --------------- | ------------------------------------------- |
| **Backend API** | `https://inpharmd-inquiry-api.onrender.com` |
| **Frontend**    | `https://inpharmd-inquiry-web.onrender.com` |


Confirm the API is up:

```bash
curl https://inpharmd-inquiry-api.onrender.com/health
# → {"status":"ok"}
```

### Generate the agent-tools secret

You only need one secret — it goes into both the Render dashboard *and* the ElevenLabs `submit_answer` tool header so they match.

```bash
openssl rand -hex 32   # → paste as AGENT_TOOLS_SECRET
```

### Set env vars on Render

In the Render dashboard → `inpharmd-inquiry-api` service → **Environment**, set:


| Key                                  | Value                                                                 |
| ------------------------------------ | --------------------------------------------------------------------- |
| `ELEVENLABS_API_KEY`                 | from ElevenLabs → Profile → API Keys                                  |
| `ELEVENLABS_INQUIRY_AGENT_ID`        | `agent_8201ksm7n8pke0wsv85wererh9ed`                                  |
| `ELEVENLABS_INQUIRY_PHONE_NUMBER_ID` | `phnum_8801ksm8ta4sfkpvc5f4qcz6a3g7`                                  |
| `AGENT_TOOLS_SECRET`                 | the secret you generated above                                        |
| `ELEVENLABS_WEBHOOK_SECRET`          | leave blank for now (optional — see note in Step 4 about HMAC signing)|


Click **Save Changes** — Render restarts the service automatically.

---

## Step 2: Twilio + Agent (already done ✓)

The following is already set up — included here for reference only:

| Thing                  | Value                                          |
| ---------------------- | ---------------------------------------------- |
| Twilio number          | `+1 470 480 2411` (purchased + voice-capable)  |
| ElevenLabs phone ID    | `phnum_8801ksm8ta4sfkpvc5f4qcz6a3g7`           |
| Agent name             | `InpharmD MI Inquiry`                          |
| Agent ID               | `agent_8201ksm7n8pke0wsv85wererh9ed`           |
| Linked                 | Phone number → agent (visible in dashboard)    |

If you ever need to re-do any of these:

- **Twilio number** → [Twilio Console](https://console.twilio.com) → Phone Numbers → buy voice-capable
- **Phone import** → ElevenLabs → **Conversational AI → Phone Numbers → Add Phone Number** → Twilio → paste Account SID + Auth Token + the number
- **Agent** → ElevenLabs → **Conversational AI → Agents → Create New Agent**

The rest of this guide configures the *behavior* of the already-created agent.

---

## Step 3: Configure Agent Settings

### Voice & Language

- **Voice**: a professional, clearly-articulated voice (e.g. `Rachel`, `Brian`, `Adam`). Manufacturer reps need to understand drug names cleanly.
- **Language**: English
- **Speed**: `1.0` (default). Don't go faster — questions are technical.

### Conversation Initiation — leave OFF

> The backend overrides the first message and passes dynamic variables directly inside `conversation_initiation_client_data` on every outbound call. You do **not** need to configure the *Conversation Initiation Client Data Webhook*. Leave it disabled.

What the backend passes per call (see `backend/call_service.py:place_inquiry_call`):


| Variable                | Meaning                                                        |
| ----------------------- | -------------------------------------------------------------- |
| `{{inquiry_id}}`        | Our internal inquiry ID — required by the `submit_answer` tool |
| `{{manufacturer_name}}` | Company being called, e.g. `Pfizer`                            |
| `{{inquiry_subject}}`   | One-line title from the form                                   |
| `{{inquiry_question}}`  | Full clinical question / detail body                           |
| `{{requester_name}}`    | Pharmacist's name (may be empty)                               |
| `{{requester_email}}`   | Pharmacist's reply-to address (may be empty)                   |


### LLM Settings


| Setting         | Value                                 |
| --------------- | ------------------------------------- |
| **Model**       | `Gemini 2.5 Flash` *or* `GPT-4o Mini` |
| **Temperature** | `0.4`                                 |
| **Max tokens**  | default                               |


Lower temperature than typical — we want faithful relay of the question, not creative paraphrasing.

### Conversation Settings


| Setting            | Value        |
| ------------------ | ------------ |
| **Max duration**   | `10 minutes` |
| **End on silence** | `30 seconds` |
| **Interruption**   | enabled      |


### System Prompt

Paste the following into the **System Prompt** field:

### First Message

**Leave blank.** The backend overrides this per call with:

> *"Hello, this is the InpharmD medical information line calling on behalf of a pharmacist with an inquiry regarding {manufacturer_name}. I need to ask about: {inquiry_subject}. Is this a good time?"*

If you want to customise the opening template, edit the `first_message` variable in `backend/call_service.py:place_inquiry_call`.

---

## Step 4: Add HTTP Tools

**Base URL**: `https://inpharmd-inquiry-api.onrender.com`

All tools use **POST** method and **Body parameters** (JSON). Every tool request must include the header `X-Agent-Secret: <AGENT_TOOLS_SECRET>` so the backend can verify the request came from your agent.

---

### Tool A: Submit Answer (REQUIRED — agent calls this near end of call)


| Field           | Value                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Name**        | `submit_answer`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| **Description** | Submit the answer captured during this call back to the InpharmD inquiry system. Call this tool ONCE, near the end of the call, just before saying goodbye. Set `outcome` based on how the call went: `"answered"` if the rep gave a clinical answer (put their words in `answer`); `"follow_up_via_email"` if they'll send the info by email; `"no_answer"` if a real person declined or couldn't answer; `"voicemail"` if you left a voicemail; `"wrong_number"` if routed to the wrong department; `"call_back_later"` if asked to call back. ALWAYS pass `inquiry_id` from the dynamic variable {{inquiry_id}}. ALWAYS pass `rep_name` if the rep gave their name. Use `rep_reference` for any case number / document name / package insert section they cited. Put caveats into `notes`. |
| **Method**      | `POST`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| **URL**         | `https://inpharmd-inquiry-api.onrender.com/api/agent-tools/submit-answer`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| **Header**      | `Content-Type: application/json`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| **Header**      | `X-Agent-Secret: <AGENT_TOOLS_SECRET>`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |


**Body Parameters:**


| Identifier      | Data Type | Required | Value Type | Description                                                                                                                                                                |
| --------------- | --------- | -------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `inquiry_id`    | Integer   | Yes      | LLM Prompt | The inquiry ID. ALWAYS pass `{{inquiry_id}}` — it's a dynamic variable available to you for every call.                                                                    |
| `outcome`       | String    | Yes      | LLM Prompt | One of: `"answered"`, `"follow_up_via_email"`, `"no_answer"`, `"voicemail"`, `"wrong_number"`, `"call_back_later"`. Pick the most accurate one based on how the call went. |
| `answer`        | String    | No       | LLM Prompt | The clinical answer the rep gave, in their own words. REQUIRED when `outcome="answered"`. Be faithful — do not interpret or rephrase clinical content.                     |
| `rep_name`      | String    | No       | LLM Prompt | Name of the person you spoke with, if they gave it (e.g. `"Sarah from Medical Affairs"`).                                                                                  |
| `rep_reference` | String    | No       | LLM Prompt | Any reference number, case ID, package-insert section, or document name the rep cited (e.g. `"Case #PFE-2026-04812"` or `"PI Section 5.4"`).                               |
| `notes`         | String    | No       | LLM Prompt | Anything extra worth keeping — off-label disclaimers, escalation paths, "they'll follow up with X in Y days".                                                              |


**Example Body (what the agent sends):**

```json
{
  "inquiry_id": 42,
  "outcome": "answered",
  "answer": "Stability data shows the product retains 95% potency after 72 hours at 30°C, per the 2024 stability study.",
  "rep_name": "Maria from Pfizer Medical Information",
  "rep_reference": "Case #PFE-2026-04812",
  "notes": "Off-label note: this temperature was tested but is not in the official labeling."
}
```

**Example Response (success):**

```json
{
  "success": true,
  "inquiry_id": 42,
  "status": "call_completed",
  "message": "Got it — answer saved."
}
```

**Example Response (when outcome is not 'answered'):**

```json
{
  "success": true,
  "inquiry_id": 42,
  "status": "call_completed",
  "message": "Call result recorded as 'voicemail'."
}
```

---

### Post-Call Webhook (REQUIRED — fires after the agent hangs up)

This is the transcript backup. Even if the agent forgot to call `submit_answer`, the post-call webhook will save the full transcript so the inquiry isn't lost.

In the agent's **Webhooks** panel → set the Post-Call URL:


| Field      | Value                                                                         |
| ---------- | ----------------------------------------------------------------------------- |
| **URL**    | `https://inpharmd-inquiry-api.onrender.com/api/webhooks/elevenlabs/post-call` |
| **Method** | `POST`                                                                        |


No custom header needed for now. ElevenLabs auto-generates a webhook signing secret in `wsec_…` format and signs each payload via HMAC-SHA256. Our backend does **not** verify that signature yet — leave `ELEVENLABS_WEBHOOK_SECRET` blank in the Render env vars so verification is skipped. The endpoint is still safe in practice because we look up the inquiry by the `conversation_id` ElevenLabs returns when we placed the call, so a spoofer would need to know a valid in-flight conversation ID to do any damage.

> When you want hardening, ping me and I'll add proper `ElevenLabs-Signature` HMAC verification using the `wsec_…` secret.

The backend matches the post-call payload to the inquiry by `conversation_id` (which we recorded when the outbound call was placed), flattens the transcript turn-by-turn, and stores both `call_transcript` and `call_summary` on the inquiry. If `submit_answer` already ran, the structured answer wins; the transcript is kept alongside for review.

---

## Step 5: Agent Conversation Examples

### Example 1 — Successful answer

> **Backend** → ElevenLabs `outbound-call` API with first_message: `"Hello, this is the InpharmD medical information line calling on behalf of a pharmacist with an inquiry regarding Pfizer. I need to ask about: Stability data for Drug X. Is this a good time?"`
>
> **Rep**: "Sure, I can help. What's your question?"
>
> **Agent**: "Thanks. The question is — what's the stability data for Drug X after a 6-hour temperature excursion at 30°C?"
>
> **Rep**: "Per our 2024 stability study, the product retains 95% potency after 72 hours at 30°C. The case reference is PFE-2026-04812."
>
> **Agent**: "Just to confirm — 95% potency at 30°C for up to 72 hours, reference PFE-2026-04812?"
>
> **Rep**: "Correct."
>
> **Agent** *(silently)* calls `submit_answer` with `outcome="answered"`, `answer="95% potency after 72 hours at 30°C, per 2024 stability study"`, `rep_name="Maria from Pfizer MI"`, `rep_reference="PFE-2026-04812"`.
>
> **Agent**: "Perfect. Thank you for your time — have a great day." *(ends call)*
>
> **Post-call webhook** fires ~5 seconds later and stores the full transcript alongside the structured answer.

### Example 2 — Rep will email back

> **Agent**: "...what's the stability data for Drug X after a 6-hour temperature excursion at 30°C?"
>
> **Rep**: "I don't have that off the top of my head — we'll need to dig into the stability records. Can we email it to you?"
>
> **Agent**: "Of course. Could you send that to [praveen@yanthraa.com](mailto:praveen@yanthraa.com) with the subject 'InpharmD inquiry: Stability data for Drug X'?"
>
> **Rep**: "Will do — expect it within 24 hours."
>
> **Agent** calls `submit_answer` with `outcome="follow_up_via_email"`, `notes="Rep will email within 24 hours"`, `rep_name="Maria"`.
>
> **Agent**: "Appreciate it. Thanks for your time." *(ends call)*

### Example 3 — Voicemail

> *(Phone rings out to voicemail)*
>
> **Agent**: "Hi, this is InpharmD calling on behalf of a pharmacist with a clinical question about Pfizer. The question is: stability data for Drug X. Could someone please call back, or send a reply to [praveen@yanthraa.com](mailto:praveen@yanthraa.com)? Thank you."
>
> **Agent** calls `submit_answer` with `outcome="voicemail"`, `notes="called during business hours, no answer"`.
>
> *(ends call)*

### Example 4 — Wrong department

> **Rep**: "This is sales — you want Medical Information. Let me transfer you."
>
> *(Transfer hangs up)*
>
> **Agent** calls `submit_answer` with `outcome="wrong_number"`, `notes="Routed to sales; transfer dropped"`.

---

## Step 6: Test the Agent

The repo ships with a test manufacturer `Yanthraa (TEST)` (id 92) so you don't disturb the 90 real US companies while iterating on the agent.


| Field        | Value                  |
| ------------ | ---------------------- |
| Manufacturer | `Yanthraa (TEST)`      |
| MI Phone     | `+919848639655`        |
| Hours        | `Mon-Sat 9a-9p IST`    |
| Email        | `praveen@yanthraa.com` |


### Step 8.1 — Verify backend env vars are loaded

```bash
cd backend && source .venv/bin/activate
python -c "import os; from dotenv import load_dotenv; load_dotenv('.env'); print('API key set:', bool(os.getenv('ELEVENLABS_API_KEY'))); print('Agent ID set:', bool(os.getenv('ELEVENLABS_INQUIRY_AGENT_ID'))); print('Phone ID set:', bool(os.getenv('ELEVENLABS_INQUIRY_PHONE_NUMBER_ID')))"
```

All three should print `True`.

### Step 8.2 — Verify the tool endpoint is reachable from the public internet

```bash
curl -X POST https://inpharmd-inquiry-api.onrender.com/api/agent-tools/submit-answer \
  -H "Content-Type: application/json" \
  -H "X-Agent-Secret: <AGENT_TOOLS_SECRET>" \
  -d '{"inquiry_id": 0, "outcome": "answered", "answer": "test"}'
```

Expected response: `{"detail":"Inquiry 0 not found"}` — that's success (it means routing + auth worked; only the inquiry lookup failed because id 0 doesn't exist).

If you get `{"detail":"Invalid agent secret"}` → header value doesn't match `AGENT_TOOLS_SECRET`.

### Step 8.3 — Place a test call

1. Open `https://inpharmd-inquiry-web.onrender.com/#inquiries`
2. Click **+ New Inquiry**
3. Pick `Yanthraa (TEST)` from the dropdown
4. Subject: `Stability question for Drug X`
5. Question: write a real clinical-sounding sentence so you can rehearse the conversation
6. Requester name + email: your own
7. Submit → the Channel Chooser appears
8. Click **Call Agent Now**
9. Your phone (+91 98486 39655) should ring. Answer it.
10. The agent should:
  - Open with the inquiry subject
    - Wait for "this is a good time"
    - Read out the full question
    - Take down your spoken answer
    - Call `submit_answer` (silently — you won't hear anything)
    - Thank you and hang up
11. Open the inquiry detail panel in the UI — within ~5 seconds you should see status `call_completed`, the structured `final_answer`, and (after another ~10 seconds) the full transcript from the post-call webhook

---

## Troubleshooting

### `503: ELEVENLABS_API_KEY is not set`

Backend env vars missing. Add the five `ELEVENLABS_`* / `AGENT_TOOLS_SECRET` lines to `backend/.env`, then **restart uvicorn** (env vars are read at startup).

### `502: ElevenLabs rejected the call` with a `phone_number` error

Wrong `ELEVENLABS_INQUIRY_PHONE_NUMBER_ID`. In the dashboard it's a string like `phn_xxx`, not the actual phone number. Re-copy from **Conversational AI → Phone Numbers**.

### `409: out_of_hours`

The manufacturer's `mi_phone_hours` field says we're outside their window. Either:

- Wait until in-hours, or
- Click **Call anyway** in the confirm dialog (the UI passes `?force=true`), or
- Edit the manufacturer row's hours via the UI

### Call connects but agent stays silent

The agent has no voice configured. Dashboard → your agent → **Voice & Language** → pick any voice.

### Call connects, agent speaks, but doesn't say the question

The system prompt wasn't pasted. The default ElevenLabs prompt makes the agent act as a generic assistant. Re-paste the prompt from Step 3 → System Prompt.

### `submit_answer` never fires (only the post-call webhook captures anything)

The tool wasn't added in the dashboard, OR the prompt is missing the "Before you say goodbye, call the submit_answer tool" instruction. Verify both. Also check **Conversational AI → Agents → your agent → Calls → [recent call] → Tool calls** — if you see `submit_answer` listed but with a 401, your `X-Agent-Secret` header doesn't match `AGENT_TOOLS_SECRET`.

### Post-call webhook never fires

Confirm the API is reachable from the public internet: `curl https://inpharmd-inquiry-api.onrender.com/health` should return `{"status":"ok"}`. Then check the webhook delivery log in **Webhooks → Logs** in the ElevenLabs dashboard — it shows every fire attempt with status code and response body.

### Webhook fires but inquiry doesn't update

The `conversation_id` in the payload didn't match what we stored. Check the inquiry row directly:

```bash
curl -s http://127.0.0.1:8000/api/inquiries/<id> | jq '.call_conversation_id'
```

Compare to the `conversation_id` shown in the ElevenLabs dashboard for that call. If they differ, the outbound-call response didn't include `conversation_id` — capture the full response with `tail -f /tmp/uvicorn.log` next time you trigger a call.

### Agent answers but reads `{{inquiry_question}}` literally

The agent's prompt is **not** treating `{{inquiry_question}}` as a template — it's reading the braces aloud. This happens when the agent doesn't recognise the variable. Make sure you copied the prompt exactly (the braces are literal in the prompt — ElevenLabs replaces them at conversation start as long as the variable was passed in `conversation_initiation_client_data.dynamic_variables`, which the backend always does).

---

## Step 6.5: Inspecting Call State

### Check a single inquiry's call status

```bash
curl -s http://127.0.0.1:8000/api/inquiries/<id> | python3 -m json.tool
```

Fields to look at:

- `status` — should be `call_pending` while ringing, `call_completed` after
- `call_conversation_id` — set when the call is placed; matched against the post-call webhook
- `call_provider_status` — `initiated` → `answered` / `voicemail` / etc. (set by `submit_answer`)
- `call_summary` — the structured answer from `submit_answer`
- `call_transcript` — full turn-by-turn from the post-call webhook
- `final_answer` — what the dashboard shows in the orange answer box

### List inquiries currently in flight

```bash
curl -s "http://127.0.0.1:8000/api/inquiries?status=call_pending" | python3 -m json.tool
```

### Manually record a result (bypassing the agent)

If you need to attach a result without going through the voice path:

```bash
curl -X POST http://127.0.0.1:8000/api/inquiries/<id>/record-call-result \
  -H "Content-Type: application/json" \
  -d '{"summary":"<answer text>","transcript":"<optional transcript>"}'
```

---

## Step 7: How Each Piece Works


| Trigger                                        | Component                                                     | What happens                                                                                                                                 |
| ---------------------------------------------- | ------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Pharmacist clicks **Call Agent Now** in the UI | `POST /api/inquiries/{id}/trigger-call`                       | Validates business hours (skippable with `?force=true`), calls `place_inquiry_call`, stores `conversation_id`, sets status to `call_pending` |
| Backend → ElevenLabs                           | `call_service.place_inquiry_call`                             | POSTs to `https://api.elevenlabs.io/v1/convai/twilio/outbound-call` with the dynamic variables + first-message override                      |
| ElevenLabs → Twilio → manufacturer             | (handled by ElevenLabs)                                       | Twilio dials the manufacturer's MI phone, voice agent speaks                                                                                 |
| Agent captures answer mid-call                 | `submit_answer` tool → `POST /api/agent-tools/submit-answer`  | Updates `call_summary`, `final_answer`, `call_provider_status`, marks `call_completed_at`                                                    |
| Agent hangs up                                 | Post-call webhook → `POST /api/webhooks/elevenlabs/post-call` | Stores `call_transcript` (turn-by-turn), backs up `call_summary` if `submit_answer` didn't fire                                              |
| Pharmacist refreshes inquiry detail            | UI fetches `GET /api/inquiries/{id}`                          | Shows the timeline, structured answer, and full transcript                                                                                   |


---

## Production Hardening Checklist

- `AGENT_TOOLS_SECRET` is a strong random value (`openssl rand -hex 32`). The tool endpoint rejects calls with wrong secrets.
- Webhook hardening (deferred): `ELEVENLABS_WEBHOOK_SECRET` is left blank. Proper HMAC verification of the `ElevenLabs-Signature` header is a follow-up task — until then, `conversation_id` matching is the safety net.
- `ELEVENLABS_API_KEY` and `DATABASE_URL` are set via Render env (never committed to git — `.env` is in `.gitignore`)
- The Twilio number is registered for A2P 10DLC if calling US numbers regularly (unregistered numbers get throttled or blocked)
- Manufacturer rows have realistic `mi_phone_hours` so the business-hours guard works. The parser understands `Mon-Fri 8a-6p ET`, `Mon-Sat 9a-9p IST`, `Mon-Fri 9-5 CT`, etc.
- Use a **dedicated** agent for inquiries (not the Nova personal assistant) so transcripts and analytics stay scoped
- The post-call webhook URL on ElevenLabs is HTTPS and reachable 24/7 (Render Starter plan or external uptime ping if using free tier)
- Only test against `Yanthraa (TEST)` while you're tuning the prompt. Real manufacturers will log every call against their MI line and may flag your Twilio number as a robocall
- When you're ready to go live, remove the `(TEST)` suffix on the Yanthraa row or delete it — make sure no one accidentally fires a real inquiry against Yanthraa's placeholder phone

---

## Appendix — What's NOT in this guide (yet)

- **Email send and inbound reply parsing.** The `Send Email` button currently only marks the inquiry as `email_sent` and starts the fallback-call countdown. Hooking up real SMTP (Gmail OAuth like Nova, or a transactional provider like Resend/Postmark) + an inbound webhook that auto-attaches manufacturer replies is a follow-up task.
- **Scheduled fallback firing.** Right now the agent only calls when you click **Call Agent Now**. If you want the system to automatically call when an email's fallback window elapses, add an APScheduler job that polls `GET /api/inquiries?status=email_sent` and triggers calls for ones past `email_sent_at + fallback_after_hours`.
- **Multi-call retries.** If the first call hits voicemail, the inquiry currently lands in `call_completed` with outcome `voicemail`. To auto-retry on the next business day, you'd add a job that finds `call_provider_status="voicemail"` inquiries older than 24h and re-triggers.

When you're ready for any of these, ping me and I'll wire them up.