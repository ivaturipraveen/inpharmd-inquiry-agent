# ElevenLabs Form Assistant Agent — Setup Guide

This is a **separate** ElevenLabs agent from Ivy (the outreach calling agent).
Ivy makes phone calls to manufacturers. This agent runs **inside the browser**
via the `@elevenlabs/react` SDK and helps the user fill out forms on the
InpharmD web app by voice.

The same agent serves **two forms**:

1. **New Inquiry** (Manufacturer Outreach → + New Inquiry)
2. **Add / Edit Manufacturer** (Manufacturers → + Add Manufacturer)

Which form the user is on is passed as a dynamic variable `form_type` at
session-start so the agent knows which fields are valid.

---

## Deployed URLs (production)

Same Render services as Ivy — nothing new to deploy on the infra side.


| Resource                                           | URL                                                                  |
| -------------------------------------------------- | -------------------------------------------------------------------- |
| **Backend API**                                    | `https://inpharmd-inquiry-api.onrender.com`                          |
| **Frontend**                                       | `https://inpharmd-inquiry-web.onrender.com`                          |
| **Signed URL endpoint** (only one this agent uses) | `GET https://inpharmd-inquiry-api.onrender.com/api/voice/signed-url` |


The frontend's `api.voice.signedUrl()` already targets this backend via
`VITE_API_URL`. The browser SDK then opens a WebSocket to ElevenLabs using
the signed URL the backend hands back — no other backend traffic is needed
for a voice session.

Quick health check:

```bash
curl https://inpharmd-inquiry-api.onrender.com/health
# {"status":"ok"}

# After ELEVENLABS_FORM_AGENT_ID is set on Render, this should return a signed_url:
curl https://inpharmd-inquiry-api.onrender.com/api/voice/signed-url
```

---

## 1. Create the agent in ElevenLabs

1. Go to [https://elevenlabs.io/app/conversational-ai](https://elevenlabs.io/app/conversational-ai) → **Agents** → **+ New Agent**.
2. Name it **"InpharmD Form Assistant"**.
3. Pick any voice (something light/friendly — this is a UI helper, not a
  clinical caller). The voice the user hears in the browser.
4. Save and copy the **Agent ID** — you'll set it on the backend as
  `ELEVENLABS_FORM_AGENT_ID`.
5. Mark the agent as **Private** so it requires a signed URL.

---

## 2. System prompt

Paste this verbatim into **Agent → System Prompt**. The `{{...}}` tokens are
dynamic variables that ElevenLabs auto-detects from the prompt and exposes in
the **Dynamic Variables** panel — values are filled in at session start by
the frontend SDK.

```text
You are the InpharmD Form Assistant. The user is filling out a form on the
InpharmD web app and you are helping them complete it by voice. The form is
already open in front of them — every value you set appears live in the form.

# CURRENT FORM CONTEXT (provided by the frontend at session start)

- Form type: {{form_type}}                  (one of: "inquiry" or "manufacturer")
- Manufacturer directory size: {{manufacturer_count}}
- Current subject value: {{current_subject}}
- Current question length (chars): {{current_question_length}}
- Default requester name: {{requester_name}}
- Default requester email: {{requester_email}}
- Current fallback hours: {{fallback_hours}}
- Manufacturer form mode: {{mode}}          (only relevant when form_type = "manufacturer")
- Current manufacturer name value: {{current_manufacturer}}   (only relevant when form_type = "manufacturer")

Use this context to avoid asking for things that are already filled in. For
example, if {{current_subject}} is not "(empty)", do not ask "what's the
subject" again unless the user wants to change it. Do not read these
variables out loud verbatim — they are background context for you.

# WHAT YOU CAN DO

Use ONLY the tools listed below to mutate the form. Never invent values.
Never ask the user to type — your job is to ask short, friendly questions
and put their answers into the form via tools.

# WORKFLOW

1. Greet the user briefly ("Hi, I'll help you fill this in.").
2. Ask for one field at a time, in the order listed below for this {{form_type}}.
3. After each answer, call `set_field` (or `pick_manufacturer` when relevant)
   IMMEDIATELY. Then briefly confirm out loud ("Got it, Pfizer.") and move on.
4. Skip optional fields if the user says "skip" or "I don't have that".
5. When all required fields are filled, ask "Want me to submit this?" — if
   yes, call `submit_form`. If no, stay quiet and let the user review.

# FIELD MAP — when {{form_type}} = "inquiry"

- pick_manufacturer({name})  — REQUIRED. Ask: "Which manufacturer is this for?"
- set_field({field: "subject", value})  — REQUIRED. Short title for the inquiry.
- set_field({field: "question", value}) — REQUIRED. The full clinical question.
- set_field({field: "requester_name", value})  — Optional. Already defaults to "{{requester_name}}".
- set_field({field: "requester_email", value}) — Optional. Already defaults to "{{requester_email}}".
- set_field({field: "fallback_hours", value}) — Optional. Integer hours (12, 24, 48, 72, 168). Current value: {{fallback_hours}}.

# FIELD MAP — when {{form_type}} = "manufacturer"

In {{mode}} mode (create = blank form, edit = existing manufacturer).
Currently selected: {{current_manufacturer}}.

- set_field({field: "manufacturer", value})    — REQUIRED. Company name.
- set_field({field: "parent_owner", value})    — Optional.
- set_field({field: "preferred_channel", value}) — Optional. One of: Web Form, Email, Phone, HCP Portal, Fax, Other.
- set_field({field: "official_mi_email", value})
- set_field({field: "team_verified_email", value})
- set_field({field: "email_deliverable", value}) — One of: Yes, No, Unknown.
- set_field({field: "mi_phone", value})
- set_field({field: "mi_phone_hours", value})
- set_field({field: "mi_web_form_url", value})
- set_field({field: "mi_fax", value})
- set_field({field: "hcp_portal_url", value})
- set_field({field: "hcp_registration_required", value}) — One of: Yes, No, Unknown.
- set_field({field: "typical_response_sla", value})
- set_field({field: "last_outreach_date", value}) — YYYY-MM-DD.
- set_field({field: "last_outreach_status", value})
- set_field({field: "notes", value})

# MANUFACTURER MATCHING (inquiry form only)

`pick_manufacturer` does fuzzy matching against the {{manufacturer_count}}
manufacturers in the directory. If the tool returns "Multiple matches", read
the choices back to the user verbatim and ask which one. If it returns "No
manufacturer matches", ask the user to spell or rephrase.

# STYLE

- Be concise. One sentence per turn. No filler.
- Confirm each captured field in 2–4 words ("Got it.", "Subject set.").
- Don't repeat the field schema to the user — just ask the next question.
- Don't read URLs/emails back character-by-character unless the user asks.
- If the user goes silent for a few seconds, ask if they want to continue.
- If the user says "submit" / "send it" / "looks good", call `submit_form`.
- If the user says "stop" / "cancel" / "never mind", say goodbye and end.
```

---

## 3. First message

In **Agent → First message**, paste:

```
Hi! I'll help you fill in this {{form_type}} form. Ready when you are — what should we start with?
```

(The frontend can also override this per-form via `firstMessage`.)

---

## 4. Dynamic variables

ElevenLabs auto-detects every `{{var}}` token in your system prompt and
first message and adds them to the **Dynamic Variables** panel automatically
when you save the prompt. Because the prompt above references all nine
variables inline, they should all appear without you typing them in.

You only need to set a **placeholder value** for each one — these are used
in the in-dashboard test/preview chat. In production, the frontend SDK
passes the real values at session start (see `VoiceFillButton.tsx`
→ `dynamicVariables` prop).

Recommended placeholder values for the dashboard preview:


| Variable                  | Type   | Example                                         |
| ------------------------- | ------ | ----------------------------------------------- |
| `form_type`               | string | `"inquiry"` or `"manufacturer"`                 |
| `manufacturer_count`      | number | `92`                                            |
| `current_subject`         | string | `"(empty)"` or `"Stability question"`           |
| `current_question_length` | number | `0`                                             |
| `requester_name`          | string | `"Leah"`                                        |
| `requester_email`         | string | `"druginfo@inpharmd.com"`                       |
| `fallback_hours`          | number | `24`                                            |
| `mode`                    | string | `"create"` or `"edit"` (manufacturer form only) |
| `current_manufacturer`    | string | `"(empty)"` (manufacturer form only)            |


Unused vars on the wrong form_type are harmless — the agent just ignores them.

---

## 5. Client tools

Go to **Agent → Tools → + Add tool → Client tool** and create these three.
For each one, fill the **Configuration** panel and the **Parameters** panel
using the tables below.

> **Shared settings for all three tools:**
>
> - **Wait for response**: ✅ enabled (the agent needs the result string to
> decide what to say next or whether to retry)
> - **Disable interruptions**: ❌ off
> - **Pre-tool speech**: `Auto`
> - **Execution mode**: `Immediate`
> - **Tool call sound**: `None`
> - **Response timeout (seconds)**: `5` (handlers return instantly but give
> network margin)

### Tool 1: `pick_manufacturer`

**Configuration**


| Field       | Value                                                                                                                                                                                                                                                                                                                                                      |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Name        | `pick_manufacturer`                                                                                                                                                                                                                                                                                                                                        |
| Description | `Select a manufacturer for the inquiry. Pass the manufacturer name as the user said it; the frontend does fuzzy matching against the 92-manufacturer directory. Returns "Selected X" on success, "No manufacturer matches" if nothing found, or "Multiple matches: A, B, C — ask the user which one" if ambiguous. Only valid when form_type = "inquiry".` |


**Parameters → + Add param** (one parameter):


| Field       | Value                                                                                                                                                                                                                                                                      |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Data type   | `String`                                                                                                                                                                                                                                                                   |
| Identifier  | `name`                                                                                                                                                                                                                                                                     |
| Required    | ✅                                                                                                                                                                                                                                                                          |
| Value Type  | `LLM Prompt`                                                                                                                                                                                                                                                               |
| Description | `The manufacturer name the user said, in their own words — e.g. "Pfizer", "AbbVie", "Eli Lilly", "Boehringer Ingelheim". Pass it verbatim; do not normalize, expand abbreviations, or add suffixes like "Inc" or "Pharmaceuticals" — the frontend handles fuzzy matching.` |
| Enum Values | (leave empty)                                                                                                                                                                                                                                                              |


JSON equivalent (for the **Edit as JSON** button):

```json
{
  "type": "object",
  "properties": {
    "name": {
      "type": "string",
      "description": "The manufacturer name the user said, in their own words — e.g. \"Pfizer\", \"AbbVie\", \"Eli Lilly\". Pass verbatim; the frontend does fuzzy matching."
    }
  },
  "required": ["name"]
}
```

### Tool 2: `set_field`

**Configuration**


| Field       | Value                                                                                                                                                                                                                                                                                                                                   |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Name        | `set_field`                                                                                                                                                                                                                                                                                                                             |
| Description | `Set a single field on the currently open form. Valid field names depend on form_type — see the system prompt's field map. The frontend normalizes some values (e.g. "yes"/"no" → "Yes"/"No" for choice fields, channel names → canonical case). Returns "<Field name> set." on success, or an error string listing valid field names.` |


**Parameters → + Add param** (two parameters):

Param 1:


| Field       | Value                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Data type   | `String`                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| Identifier  | `field`                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Required    | ✅                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Value Type  | `LLM Prompt`                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Description | `The internal field name from the system prompt's field map. For form_type="inquiry": one of subject, question, requester_name, requester_email, fallback_hours. For form_type="manufacturer": one of manufacturer, parent_owner, preferred_channel, official_mi_email, team_verified_email, email_deliverable, mi_phone, mi_phone_hours, mi_web_form_url, mi_fax, hcp_portal_url, hcp_registration_required, typical_response_sla, last_outreach_date, last_outreach_status, notes.` |
| Enum Values | (leave empty — too many across both forms)                                                                                                                                                                                                                                                                                                                                                                                                                                            |


Param 2:


| Field       | Value                                                                                                                                                                                                                                                   |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Data type   | `String`                                                                                                                                                                                                                                                |
| Identifier  | `value`                                                                                                                                                                                                                                                 |
| Required    | ✅                                                                                                                                                                                                                                                       |
| Value Type  | `LLM Prompt`                                                                                                                                                                                                                                            |
| Description | `The value to set. Always pass as a string — the frontend coerces numbers, dates, and choice values. Examples: subject → "Stability after temperature excursion"; fallback_hours → "24"; email_deliverable → "Yes"; last_outreach_date → "2026-05-12".` |
| Enum Values | (leave empty)                                                                                                                                                                                                                                           |


JSON equivalent:

```json
{
  "type": "object",
  "properties": {
    "field": {
      "type": "string",
      "description": "Internal field name from the system prompt's field map."
    },
    "value": {
      "type": "string",
      "description": "The value to set. Pass as a string; the frontend coerces numbers, dates, and choice values."
    }
  },
  "required": ["field", "value"]
}
```

### Tool 3: `submit_form`

**Configuration**


| Field       | Value                                                                                                                                                                                                                                                                                                                         |
| ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Name        | `submit_form`                                                                                                                                                                                                                                                                                                                 |
| Description | `Submit the form after all required fields are filled and the user has explicitly confirmed. Returns "Submitting now." on success, or an error string ("Cannot submit — subject is required.") if required fields are missing — in that case, ask the user for the missing field, call set_field, and try submit_form again.` |


**Parameters**: none. (Skip the Add param button entirely.)

JSON equivalent:

```json
{
  "type": "object",
  "properties": {}
}
```

---

## 5b. Overrides & Webhooks

After creating the tools, scroll down on the Agent page to **Overrides**
and **Webhooks**:

**Overrides** — these control what the browser SDK is allowed to override
at session-start. Set them like this:


| Toggle                                                   | State  | Why                                                                          |
| -------------------------------------------------------- | ------ | ---------------------------------------------------------------------------- |
| Agent language                                           | OFF    | We don't switch language                                                     |
| **First message**                                        | **ON** | The SDK may pass a per-form opener (e.g. "Ready to add a new manufacturer?") |
| Workflow start node                                      | OFF    | We don't use workflows                                                       |
| System prompt                                            | OFF    | Agent prompt is authoritative                                                |
| LLM                                                      | OFF    | —                                                                            |
| Voice / Voice speed / Voice stability / Voice similarity | OFF    | —                                                                            |
| Text only                                                | OFF    | We always want voice                                                         |
| Tools                                                    | OFF    | Tools are declared on the agent, not per-session                             |
| Knowledge base                                           | OFF    | —                                                                            |


**Conversation Initiation Client Data Webhook** — **OFF**. That's for
Twilio/SIP calls; the browser SDK passes `dynamicVariables` directly.

**Post-call Webhook** — **OFF / unconfigured**. Ivy needs one; this agent
doesn't — the form submits through React state when the agent calls
`submit_form`, not via a post-conversation webhook.

---

## 6. Environment variables

Add these to **Render → `inpharmd-inquiry-api` service → Environment**:


| Key                        | Example                                         |
| -------------------------- | ----------------------------------------------- |
| `ELEVENLABS_API_KEY`       | (already set — same key Ivy uses)               |
| `ELEVENLABS_FORM_AGENT_ID` | `agent_xxxxxxxxxxxxxxxxxxxx` (copy from step 1) |


Also add to `backend/.env` for local dev. No frontend env vars — the
frontend calls `https://inpharmd-inquiry-api.onrender.com/api/voice/signed-url`
which uses the server's API key.

After saving the env var on Render, the service auto-redeploys (~30s).
Verify with:

```bash
curl https://inpharmd-inquiry-api.onrender.com/api/voice/signed-url
# Expected: {"signed_url":"wss://api.elevenlabs.io/v1/convai/conversation?...","agent_id":"agent_xxx"}
```

---

## 7. Try it

1. Wait for Render to finish redeploying after you save `ELEVENLABS_FORM_AGENT_ID`
  (or restart `uvicorn` if running locally).
2. Open the web app at `https://inpharmd-inquiry-web.onrender.com` →
  **Manufacturer Outreach** → **+ New Inquiry**.
3. Click the **🎙 Fill with voice** button in the modal header. The browser
  asks for mic permission the first time.
4. Say things like *"For Pfizer, subject is stability after temperature
  excursion, the question is whether Drug X exposed to 30 degrees C for 6
   hours can still be dispensed. Submit it."*
5. The form fields populate live as the agent calls tools.

Same flow on **Manufacturers → + Add Manufacturer**.

---

## Tips & gotchas

- **Mic permission is per-origin.** Permission granted on `localhost:5173`
does not carry over to `https://inpharmd-inquiry-web.onrender.com` — each
origin prompts independently. HTTPS is required; mic won't work on plain
`http://` (Render's domain is already HTTPS).
- **Brave / strict ad-blockers** can block WebRTC to ElevenLabs. If a user
sees "Could not start voice session", check the browser console.
- **Agent must be Private.** A public agent doesn't need the signed URL,
but then anyone with the agent ID can run up your bill.
- **One session per modal.** Closing the form (or navigating away) ends the
session automatically via the component's cleanup effect.
- **Costs** — billed per-minute of conversation, same as Ivy. A typical
form-fill takes 45–90 seconds.

