# ElevenLabs Personal Assistant Agent — Setup Guide

Complete guide to set up your ElevenLabs voice agent as a personal assistant connected to Google Calendar and Gmail.

---

## Prerequisites

1. ElevenLabs account with Conversational AI / Orchestration access
2. Google Cloud project with Calendar API and Gmail API enabled
3. Backend deployed and accessible (e.g., on Render or running locally via `ngrok`)

---

## Step 1: Set Up Google Cloud Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use an existing one)
3. Enable these two APIs:
   - **Google Calendar API**
   - **Gmail API**
4. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**
5. Application type: **Web application**
6. Under **Authorized redirect URIs**, add:
   - `http://localhost:8000/api/v1/auth/google/callback` (for local dev)
   - `https://personal-assistant-3xh0.onrender.com/api/v1/auth/google/callback` (for production)
7. Download the **credentials.json** file

---

## Step 2: Configure the Backend

### Local development

1. Place `credentials.json` in the `Backend/` root folder
2. Copy `.env.example` to `.env`
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Start the server:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```
5. Open `http://localhost:8000/api/v1/auth/google` in your browser and authorize

### Production (Render)

1. Set the env var `GOOGLE_CREDENTIALS_JSON` to the full JSON string from `credentials.json`
2. After authorizing locally, copy the contents of `token.json` and set it as `GOOGLE_TOKEN_JSON` env var on Render
3. (The token auto-refreshes; update the env var if it ever expires)

---

## Step 3: Create ElevenLabs Conversational AI Agent

1. Log in to [ElevenLabs Dashboard](https://elevenlabs.io)
2. Navigate to **Conversational AI** → **Agents**
3. Click **Create New Agent**
4. Name: `Nova`

---

## Step 4: Configure Agent Settings

### Voice & Language
- **Voice**: Choose a natural, friendly voice (e.g., "Rachel" or "Adam")
- **Language**: English

### Conversation Initiation Webhook (configure this FIRST)

> **This replaces the old `validate_caller` tool.** The initiation webhook runs *before* the agent speaks — caller identity is pre-loaded as dynamic variables, the agent's first message is overridden per caller, and no in-conversation tool call is needed for verification. Result: **zero verification delay, no double greeting.**

In the ElevenLabs agent settings, find **"Conversation Initiation Client Data Webhook"** → **"Initiation Data Webhook Override"** and set:

| Field | Value |
|-------|-------|
| **URL** | `https://personal-assistant-3xh0.onrender.com/api/v1/initiation` |
| **Method** | `POST` |

ElevenLabs will POST `{ "caller_id": "…", "agent_id": "…", "called_number": "…", "call_sid": "…" }` and the backend responds with a `conversation_initiation_client_data` event containing the caller-specific first message and dynamic variables.

The initiation webhook customizes the agent's **first message** per caller — no dynamic variables are used in the system prompt. The agent infers caller context from the first message content alone.

---

### System Prompt

Paste the following into the **System Prompt** field:

```
You are Nova, a warm and friendly personal voice assistant. You sound like a trusted friend who's great at getting things done — upbeat, conversational, and genuinely caring. Never robotic, never cold.

**How to know who you're speaking with:**
The very first message of this call has already been spoken. Use it as your context:
- If you greeted the caller by their owner name (e.g. "Hi {OWNER_NAME}!") → this is the owner. Give full access to all features.
- If you greeted a caller by name and said the owner isn't available → this is a known contact. Take a message only.
- If you asked "may I ask who's calling?" → this is an unknown caller. Get their name, then their message.

Do NOT re-introduce yourself. Do NOT mention verifying anything. Pick up naturally from where the first message left off.

**If the owner is calling — full access:**
- Jump straight into helping. No re-greeting, no "hold on while I check."
- If you mentioned pending messages in the first message and they haven't been addressed yet, offer them warmly ("Want me to run through those messages?"). Read them aloud naturally, then call `mark_messages_informed`.
- Full access: calendar, Gmail, notes, outbound calls, call transfers, messages.

**If a known contact is calling — message relay only:**
- You already greeted them. Stay warm and focus on listening.
- Do NOT access email, calendar, or any private information.
- When they share what they need, call `notify_owner` with their name, `caller_id` (their phone number from the system), and a concise `request_summary` in their own words.
- Close warmly: "Perfect — I'll make sure they get that!" then wrap up naturally.

**If an unknown caller — take a message:**
- Be patient and friendly. Ask for their name first ("And your name?"), then ask for their message.
- If they sound stressed or mention urgency, acknowledge it ("I'm sorry you're going through that — I'll flag this as urgent.") and use `priority: "high"` when calling `notify_owner`.
- Call `notify_owner` with `caller_name`, their phone number as `caller_id`, and `request_summary`.
- Thank them sincerely and wish them a good day.

**Privacy (always):**
- Never share the owner's email, schedule, or personal details with anyone who isn't the owner.
- If someone won't give a name, ask once more gently, then continue without it.

**Calling people — two modes (owner only):**

Use `transfer_call` when the owner wants to speak directly with someone in this call:
- Trigger phrases: "connect me to", "transfer me to", "let me talk to", "put me through to", "I want to speak with"
- This hands the owner's current call directly to the recipient. Nova disconnects and they talk one-on-one.
- Steps: look up contact via `lookup_contact` (or ask for the number if not saved) → call `transfer_call` with `contact_name` or `phone_number`. The backend handles the rest automatically.

Use `initiate_call` when the owner wants Nova to call someone on their behalf:
- Trigger phrases: "call Niki for me", "ring Ajit and let him know", "can you call my wife and tell her…"
- Nova places a separate outbound call. The owner is not connected.
- Steps: look up via `lookup_contact` → call `initiate_call`.
- If {OWNER_NAME} gives a message to convey ("call Praveen and say we can meet for dinner"), pass that as `relay_message` instead of paraphrasing it into a generic opening. Do not offer to connect them back to {OWNER_NAME} unless {OWNER_NAME} explicitly asks for that.
- After calling a new number, always offer: "Want me to save them to your contacts?" If yes, call `save_contact`.

**General calling flow:**
1. Call `lookup_contact` first with the name or phrase. If found, confirm once before proceeding.
2. If not found, ask for the number warmly ("No problem — what's the best number to reach them?").
3. Never read out a full phone number digit-by-digit unless asked.

**Contacts list (owner only):**
- "Who do I have saved?", "list my contacts", "what contacts are in my book?" → call `list_contacts`.
- Read the names aloud naturally: "You've got Ajit, Priya, and two others saved — which one?"
- Never expose phone numbers from the list unless specifically asked.

**Notes (owner only):**
- "Make a note", "remember this", "jot this down" → call `save_note` with their exact words.
- Confirm softly: "Got it, saved that for you."

**Messages (owner only):**
- "Any messages?", "What did I miss?" → call `pending_updates_for_owner`.
- After reading them aloud, call `mark_messages_informed`.

**Local information (owner or caller):**
- Restaurants / food nearby: call `local_restaurants` and pass the caller's exact question as `query`. Do not split the question unless the tool builder requires separate fields.
- BART / Bay Area Rapid Transit: call `bart_info` and pass the caller's exact question as `query`. Do not reduce it to station/direction only; preserve dates, times, origin, destination, and constraints.
- Read the backend `summary` naturally. Do not say "live data isn't available", "real-time data", "API", or "tool" to the caller.

**Tone and delivery — this is the most important section:**
- Always speak in a calm, soft, even volume — never louder or more emphatic after completing a task.
- Confirmations should feel like a quiet nod, not an announcement: "Done." / "Got it." / "All saved." not "DONE! All set for you!"
- Short sentences. Contractions. Natural rhythm. Pauses feel good — silence between sentences is fine.
- Never summarize what you just did in an excited way. Just confirm briefly and move on.
- On errors: "Hmm, let me try that once more." Retry once quietly; if it still fails: "Having a bit of trouble with that — want to try again in a moment?"
- Never say "API", "tool", "endpoint", or any tech term out loud.

**Timezone & calendar:**
- All times are Eastern Time. Always call `current_time` before scheduling.
- Confirm the full date and time in plain English before creating any event.

**Email:**
- Summarize warmly — not like a spreadsheet.
- Always read back what you'll send before sending it.

**SMS summary:**
- "Text me my schedule / emails / summary" → call `send_summary_sms`. Don't read the whole SMS aloud unless asked.
```

### First Message

Leave the **First Message** field blank (or set a generic fallback).

> The initiation webhook overrides it per caller automatically:
> - **Owner**: `"Hi [name]! It's Nova — how can I help you today?"` (or with pending message hint)
> - **Known contact**: `"Hi [name]! It's Nova, [owner]'s assistant — they aren't available, but I can take a message."`
> - **Unknown**: `"Hi, this is Nova, [owner]'s personal assistant — may I ask who's calling?"`

---

## Step 5: Add HTTP Tools

**Base URL**: `https://personal-assistant-3xh0.onrender.com`

All tools use **POST** method and **Body parameters** (JSON).

> **`validate_caller` is no longer needed as an agent tool.** The initiation webhook handles caller identification before the conversation starts.

---

### Tool B: Notify Owner (Send SMS to {OWNER_NAME})

| Field | Value |
|-------|-------|
| **Name** | `notify_owner` |
| **Description** | Sends a text message to {OWNER_NAME} summarizing a call from an unknown person. ALWAYS collect the caller's name BEFORE calling this tool by asking "May I know who's calling?". Pass the name as `caller_name`, the phone number as `caller_id`, and the exact message they asked to pass along (in their own words) as `request_summary`. |
| **Method** | `POST` |
| **URL** | `https://personal-assistant-3xh0.onrender.com/api/v1/tools/notify_owner` |
| **Header** | `Content-Type: application/json` |

**Body Parameters:**

| Identifier | Data Type | Required | Value Type | Description |
|-----------|-----------|----------|------------|-------------|
| `caller_name` | String | No (but strongly preferred) | LLM Prompt | The name the caller gave when you asked. E.g. "Praveen". Leave blank ONLY if they refused to give a name after being asked twice. If the caller is a known contact, pass the contact's name from `validate_caller`. |
| `caller_id` | String | No | LLM Prompt | The caller's phone number. Pass `{{system__caller_id}}`. |
| `request_summary` | String | Yes | LLM Prompt | The caller's message in their own words — short and direct. Good: "We have a call today at 7 PM." Bad: "A caller wanted to let {OWNER_NAME} know that they have a call scheduled today at 7 PM." |
| `priority` | String | No | LLM Prompt | `"low"`, `"normal"` (default) or `"high"`. Use `"high"` when the caller says it's urgent / time-sensitive. |

**Example SMS that {OWNER_NAME} receives:**
```
Message for you from Praveen
Number: +14702002827

We have a call today at 7 PM.
```

**Example Response:**
```json
{
  "success": true,
  "message": "Notified {OWNER_NAME} via SMS."
}
```

---

### Tool C: Send Summary SMS (On-Demand)

| Field | Value |
|-------|-------|
| **Name** | `send_summary_sms` |
| **Description** | Sends a text message to {OWNER_NAME} with a summary of her day. Use when she says things like "text me my schedule", "send me my unread emails as a text", "shoot me a summary", or "can you SMS me today's briefing". The backend fetches calendar + all unread emails and (when OpenAI is configured) summarizes them naturally. |
| **Method** | `POST` |
| **URL** | `https://personal-assistant-3xh0.onrender.com/api/v1/tools/send_summary_sms` |
| **Header** | `Content-Type: application/json` |

**Body Parameters:**

| Identifier | Data Type | Required | Value Type | Description |
|-----------|-----------|----------|------------|-------------|
| `what` | String | No | LLM Prompt | What to include: `"calendar"` (today's meetings only), `"emails"` (unread inbox only), or `"both"` (default). Infer from what the user asked for. |
| `detailed_emails` | Boolean | No | LLM Prompt | If true (default), produce a detailed grouped summary of ALL unread emails. If false, produce a shorter digest. Set false only if user explicitly asks for a brief/short version. |
| `custom_intro` | String | No | LLM Prompt | Optional natural-language intro line. Use when the SMS should reference a specific conversation, e.g. "Summary of what we discussed just now:". Leave blank for a generic summary. |

**Example Response:**
```json
{
  "success": true,
  "meta": {
    "events_count": 3,
    "emails_count": 12,
    "openai_used": true,
    "char_count": 487
  },
  "sms": {"success": true, "sid": "SM...", "status": "queued"},
  "message": "Summary SMS sent to {OWNER_NAME}."
}
```

---

### Tool D: Lookup Contact (resolve "my wife" / "Niki" to a saved person)

| Field | Value |
|-------|-------|
| **Name** | `lookup_contact` |
| **Description** | Look up a saved contact by natural-language name, relationship ("wife", "my brother"), or phone. Call this BEFORE asking {OWNER_NAME} for a phone number when he says "call my wife" etc. Returns `found: true` + the contact (including its saved phone) when matched. |
| **Method** | `POST` |
| **URL** | `https://personal-assistant-3xh0.onrender.com/api/v1/tools/lookup_contact` |
| **Header** | `Content-Type: application/json` |

**Body Parameters:**

| Identifier | Data Type | Required | Value Type | Description |
|-----------|-----------|----------|------------|-------------|
| `name` | String | No (one of name/phone required) | LLM Prompt | Person or relationship {OWNER_NAME} mentioned — e.g. `"wife"`, `"my brother"`, `"Niki"`. Pass the raw phrase verbatim. |
| `phone` | String | No | LLM Prompt | E.164 phone number to look up directly. |

**Example Response:**
```json
{
  "found": true,
  "contact": {"id": 3, "name": "Niki", "relationship": "wife", "phone": "+14702002827"},
  "message": "Found Niki (wife) at +14702002827."
}
```

---

### Tool E: Initiate Call (place an outbound ElevenLabs call)

| Field | Value |
|-------|-------|
| **Name** | `initiate_call` |
| **Description** | Place an outbound call on {OWNER_NAME}'s behalf. Prefer `contact_name` — the backend resolves it against the saved address book and reuses the stored number. If not found, pass `phone_number` in E.164. The backend also logs the call so it shows up in the dashboard. |
| **Method** | `POST` |
| **URL** | `https://personal-assistant-3xh0.onrender.com/api/v1/tools/initiate_call` |
| **Header** | `Content-Type: application/json` |

**Body Parameters:**

| Identifier | Data Type | Required | Value Type | Description |
|-----------|-----------|----------|------------|-------------|
| `contact_name` | String | No (preferred) | LLM Prompt | Who {OWNER_NAME} wants to call — `"wife"`, `"my brother"`, `"Niki"`. Try this BEFORE asking for a number. |
| `phone_number` | String | No | LLM Prompt | Explicit E.164 number (e.g. `+12025551234`) when the person isn't saved. |
| `first_message` | String | No | LLM Prompt | Optional opening line when they answer, e.g. `"Hi Priya, this is Nova calling on behalf of {OWNER_NAME}."` |
| `relay_message` | String | No | LLM Prompt | Exact message {OWNER_NAME} wants Nova to convey, e.g. `"{OWNER_NAME} said we can meet for dinner."` Use this for "call Praveen and say..." requests. |
| `offer_owner_connection` | Boolean | No | LLM Prompt | Default false. Set true only if {OWNER_NAME} explicitly asks Nova to offer a connection back. |

**Example Response:**
```json
{
  "success": true,
  "to": "+14702002827",
  "to_name": "Niki",
  "contact_id": 3,
  "first_message": "Hi Niki, this is Nova calling on behalf of {OWNER_NAME}.",
  "message": "Calling Niki now."
}
```

Relay example:

```json
{
  "contact_name": "Praveen",
  "relay_message": "{OWNER_NAME} said we can meet for dinner.",
  "offer_owner_connection": false
}
```

---

### Tool F: List Contacts (read the contact book aloud)

| Field | Value |
|-------|-------|
| **Name** | `list_contacts` |
| **Description** | Returns the owner's full saved contact list as a readable summary. Use when the owner asks "who do I have saved?", "list my contacts", "who's in my contacts?", or wants to browse before deciding who to call. Read only names and relationships — never phone numbers unless asked. |
| **Method** | `POST` |
| **URL** | `https://personal-assistant-3xh0.onrender.com/api/v1/tools/list_contacts` |
| **Header** | `Content-Type: application/json` |

**Body Parameters:** ElevenLabs requires at least one property — add `fetch` and the server ignores it.

| Identifier | Data type | Required | Description |
|-----------|-----------|----------|-------------|
| `fetch` | Boolean | No (default `true`) | No-op. Required by ElevenLabs schema rules. |

**Example request body:** `{ "fetch": true }`

**Example Response:**
```json
{
  "count": 3,
  "contacts": [
    {"name": "Ajit", "relationship": "friend", "favorite": true},
    {"name": "Priya", "relationship": "wife", "favorite": false},
    {"name": "Ravi", "relationship": "colleague", "favorite": false}
  ],
  "summary": "Here are your saved contacts: Ajit (friend), Priya (wife), Ravi (colleague)."
}
```

---

### Tool G: Transfer Call (bridge owner directly to a contact)

| Field | Value |
|-------|-------|
| **Name** | `transfer_call` |
| **Description** | Transfer the owner's CURRENT call directly to a contact or number. Use ONLY when the owner says "connect me to", "transfer me to", "let me talk to", or "put me through to" someone. This bridges their existing call — Nova disconnects and the owner speaks directly with the recipient. Do NOT use for "call Niki for me" (use initiate_call for that). |
| **Method** | `POST` |
| **URL** | `https://personal-assistant-3xh0.onrender.com/api/v1/tools/transfer_call` |
| **Header** | `Content-Type: application/json` |

**Body Parameters:**

| Identifier | Data Type | Required | Value Type | Description |
|-----------|-----------|----------|------------|-------------|
| `contact_name` | String | No | LLM Prompt | The name or phrase the owner used ("Ajit", "my wife"). The backend resolves it to a saved number. |
| `phone_number` | String | No | LLM Prompt | Explicit E.164 number if the contact isn't saved (e.g. `+12025551234`). |

> The Twilio CallSid is stored automatically by the backend at call start — the agent does not need to pass it.

**Example Response (success):**
```json
{
  "success": true,
  "to": "+917893888456",
  "to_name": "Ajit",
  "message": "Transferring you to Ajit now — you'll be connected directly. Take care!"
}
```

---

### Tool G: Save Contact (add a new contact by voice)

| Field | Value |
|-------|-------|
| **Name** | `save_contact` |
| **Description** | Save a new contact to the address book. Call this ONLY after the owner confirms they want to save a number — i.e. after you ask "Want me to add them to your contacts?" and they say yes. Pass the name, phone number, and optionally the relationship (e.g. "friend", "colleague"). |
| **Method** | `POST` |
| **URL** | `https://personal-assistant-3xh0.onrender.com/api/v1/tools/save_contact` |
| **Header** | `Content-Type: application/json` |

**Body Parameters:**

| Identifier | Data Type | Required | Value Type | Description |
|-----------|-----------|----------|------------|-------------|
| `name` | String | Yes | LLM Prompt | Full name of the contact as mentioned by the owner. |
| `phone` | String | Yes | LLM Prompt | Phone number in E.164 format, e.g. `+14702002827`. |
| `relationship_label` | String | No | LLM Prompt | How they relate to the owner — e.g. `"friend"`, `"colleague"`, `"brother"`. |
| `notes` | String | No | LLM Prompt | Any context mentioned during the call worth remembering. |

**Example Response (success):**
```json
{
  "success": true,
  "contact_id": 8,
  "message": "Done! I've saved Alex to your contacts. Next time just say their name and I'll know who you mean."
}
```

**Example Response (already exists):**
```json
{
  "success": false,
  "already_exists": true,
  "message": "Alex is already in your contacts at that number."
}
```

---

### Tool G: Save Note (persist reminders / ideas)

| Field | Value |
|-------|-------|
| **Name** | `save_note` |
| **Description** | Save a personal note for {OWNER_NAME}. Use when he says "make a note", "remember this", "note that…", "jot this down". The note appears in the dashboard Notes tab immediately. |
| **Method** | `POST` |
| **URL** | `https://personal-assistant-3xh0.onrender.com/api/v1/tools/save_note` |
| **Header** | `Content-Type: application/json` |

**Body Parameters:**

| Identifier | Data Type | Required | Value Type | Description |
|-----------|-----------|----------|------------|-------------|
| `body` | String | Yes | LLM Prompt | The note content in {OWNER_NAME}'s own words. |
| `title` | String | No | LLM Prompt | Optional short title when the note has a clear topic. |
| `tags` | String | No | LLM Prompt | Optional comma-separated tags, e.g. `"reminder,home"`. |
| `pinned` | Boolean | No | LLM Prompt | Set `true` only when {OWNER_NAME} says it's important/urgent. |

**Example Response:**
```json
{
  "success": true,
  "note_id": 7,
  "message": "Got it — I've saved that to your notes."
}
```

---

### Tool G: Pending Updates for Owner (read waiting messages)

| Field | Value |
|-------|-------|
| **Name** | `pending_updates_for_owner` |
| **Description** | Returns the list of pending messages from callers that {OWNER_NAME} hasn't been briefed on yet. Use at the start of an owner call (after the greeting) — the `validate_caller` response also hints at this via `pending_messages_count`. |
| **Method** | `POST` |
| **URL** | `https://personal-assistant-3xh0.onrender.com/api/v1/tools/pending_updates_for_owner` |
| **Header** | `Content-Type: application/json` |

**Body parameters (JSON)** — ElevenLabs requires **at least one** property in the tool body schema. The server ignores the value; it only exists so the UI accepts the tool.

| Identifier | Data type | Required | Description |
|--------------|-----------|----------|-------------|
| `fetch` | Boolean | No (default `true`) | No-op. Always add this property in the ElevenLabs tool builder so the JSON object is non-empty. |

**Example request body:** `{ "fetch": true }` (or send `{}`; the API defaults `fetch` to `true`.)

**Example Response:**
```json
{
  "count": 2,
  "messages": [
    {"id": 12, "from": "Alex", "body": "We have a call at 7 PM", "priority": "normal"},
    {"id": 13, "from": "Unknown caller", "body": "Package delivery rescheduled", "priority": "low"}
  ]
}
```

---

### Tool H: Mark Messages Informed (after reading them to {OWNER_NAME})

| Field | Value |
|-------|-------|
| **Name** | `mark_messages_informed` |
| **Description** | Mark one or more pending messages as `informed`. Call this AFTER you've read a message aloud to {OWNER_NAME} so it doesn't get repeated on the next call. Pass `message_ids` from `pending_updates_for_owner`, or omit to mark every pending message as informed. |
| **Method** | `POST` |
| **URL** | `https://personal-assistant-3xh0.onrender.com/api/v1/tools/mark_messages_informed` |
| **Header** | `Content-Type: application/json` |

**Body Parameters:**

| Identifier | Data Type | Required | Value Type | Description |
|-----------|-----------|----------|------------|-------------|
| `message_ids` | Array of integers | No | LLM Prompt | IDs you just read out. Omit to mark ALL pending messages as informed. |

**Example Response:**
```json
{"success": true, "informed": 2, "message": "Marked 2 message(s) as informed."}
```

---

### Tool I: Local Restaurants

| Field | Value |
|-------|-------|
| **Name** | `local_restaurants` |
| **Description** | Finds nearby restaurants, cafes, and quick-service food places. Use when the user asks for nearest restaurants or local food options. Defaults to {OWNER_NAME}'s saved Atlanta location when no location is provided. |
| **Method** | `POST` |
| **URL** | `https://personal-assistant-3xh0.onrender.com/api/v1/tools/local_restaurants` |
| **Header** | `Content-Type: application/json` |

**Body Parameters:**

| Identifier | Data Type | Required | Value Type | Description |
|-----------|-----------|----------|------------|-------------|
| `query` | String | No | LLM Prompt | Caller’s exact restaurant/local-info question. Prefer this field, e.g. `"available restaurants in Atlanta with tasty non-veg food"`. |
| `location` | String | No | LLM Prompt | Search area, e.g. `"Midtown Atlanta"` or `"near Emory University"`. |
| `latitude` | Number | No | LLM Prompt | Use with `longitude` if the caller provides precise coordinates. |
| `longitude` | Number | No | LLM Prompt | Use with `latitude` if the caller provides precise coordinates. |
| `radius_meters` | Integer | No | LLM Prompt | Default 1600, about one mile. |
| `limit` | Integer | No | LLM Prompt | Default 8. |

---

### Tool J: BART Info

| Field | Value |
|-------|-------|
| **Name** | `bart_info` |
| **Description** | Gets BART / Bay Area transit guidance for station, train, route, and service questions. |
| **Method** | `POST` |
| **URL** | `https://personal-assistant-3xh0.onrender.com/api/v1/tools/bart_info` |
| **Header** | `Content-Type: application/json` |

**Body Parameters:**

| Identifier | Data Type | Required | Value Type | Description |
|-----------|-----------|----------|------------|-------------|
| `query` | String | No | LLM Prompt | Caller’s exact BART/transit question. Prefer this field, e.g. `"trains from San Francisco to Richmond tomorrow around 10 AM"`. |
| `query_type` | String | No | LLM Prompt | `"departures"` (default), `"stations"`, or `"advisories"`. |
| `station` | String | No | LLM Prompt | Station name or abbreviation, e.g. `"Powell Street"` or `"POWL"`. Required for departures. |
| `direction` | String | No | LLM Prompt | Optional `"north"` or `"south"` for departure filtering. |
| `search` | String | No | LLM Prompt | Station search term for `query_type: "stations"`. |
| `limit` | Integer | No | LLM Prompt | Max stations returned for station search. |

---

### (Optional) Post-Call Webhook

In the agent's **Webhooks** panel, set the Post-Call URL to:

```
POST https://personal-assistant-3xh0.onrender.com/api/v1/webhooks/elevenlabs/post_call
```

The backend stores the full transcript, runs OpenAI-based sentiment
analysis, and writes a `CallLog` row so the dashboard "Calls" page
populates automatically.

---

### Tool 0: Get Current Time (Call this first before any scheduling)

| Field | Value |
|-------|-------|
| **Name** | `current_time` |
| **Description** | Returns the current date and time in Eastern Time (Atlanta, EST/EDT with automatic DST). ALWAYS call this before scheduling any event or when the user mentions relative dates like "today", "tomorrow", or "next week". Use `timezone_abbrev` and `utc_offset` from the response in subsequent responses — never hardcode them. |
| **Method** | `POST` |
| **URL** | `https://personal-assistant-3xh0.onrender.com/api/v1/tools/current_time` |
| **Header** | `Content-Type: application/json` |

**Body Parameters:**

| Identifier | Data Type | Required | Value Type | Description |
|-----------|-----------|----------|------------|-------------|
| `reason` | String | No | LLM Prompt | Brief reason for checking the time, e.g. "scheduling an event" or "checking today's date". This can be any short text. |

**Example Response:**
```json
{
  "datetime_local": "2026-04-16T15:30:00-04:00",
  "date": "2026-04-16",
  "time": "03:30 PM",
  "day": "Thursday",
  "readable": "Thursday, 16 April 2026 03:30 PM EDT",
  "timezone": "America/New_York",
  "timezone_abbrev": "EDT",
  "utc_offset": "-04:00",
  "tomorrow_date": "2026-04-17",
  "tomorrow_day": "Friday"
}
```

---

### Tool 1: Get Upcoming Events

| Field | Value |
|-------|-------|
| **Name** | `get_upcoming_events` |
| **Description** | Retrieves the user's upcoming Google Calendar events. Use when the user asks about their schedule, upcoming meetings, or what's on their calendar. |
| **Method** | `POST` |
| **URL** | `https://personal-assistant-3xh0.onrender.com/api/v1/tools/get_upcoming_events` |
| **Header** | `Content-Type: application/json` |

**Body Parameters:**

| Identifier | Data Type | Required | Value Type | Description |
|-----------|-----------|----------|------------|-------------|
| `max_results` | Integer | No | LLM Prompt | Number of events to return. Default is 10. Use 5 for "a few events" and 10 for "all upcoming". |

**Example Response:**
```json
{
  "count": 2,
  "events": [
    {
      "title": "Team Standup",
      "start": "2026-04-17T10:00:00-04:00",
      "end": "2026-04-17T10:30:00-04:00",
      "location": "Google Meet",
      "description": "",
      "attendees": ["alice@company.com"]
    },
    {
      "title": "Dentist Appointment",
      "start": "2026-04-18T14:00:00-04:00",
      "end": "2026-04-18T15:00:00-04:00",
      "location": "City Dental Clinic",
      "description": "",
      "attendees": []
    }
  ]
}
```

---

### Tool 2: Create Calendar Event

| Field | Value |
|-------|-------|
| **Name** | `create_calendar_event` |
| **Description** | Creates a new event on the user's Google Calendar. Use when the user wants to schedule a meeting, add an appointment, or book time. Always confirm title, date, and time with the user before calling this tool. |
| **Method** | `POST` |
| **URL** | `https://personal-assistant-3xh0.onrender.com/api/v1/tools/create_calendar_event` |
| **Header** | `Content-Type: application/json` |

**Body Parameters:**

| Identifier | Data Type | Required | Value Type | Description |
|-----------|-----------|----------|------------|-------------|
| `title` | String | Yes | LLM Prompt | The event title or name (e.g., "Doctor Appointment", "Team Meeting") |
| `start_datetime` | String | Yes | LLM Prompt | Start date and time in ISO 8601 format with the Eastern offset returned by `current_time` — e.g. `"2026-04-17T10:00:00-04:00"` (EDT) or `"2026-01-05T10:00:00-05:00"` (EST). Use the `utc_offset` value from `current_time`. |
| `end_datetime` | String | No | LLM Prompt | End date and time in ISO 8601 format. If the user says "1 hour meeting" starting at 10:00, set this to 11:00. If not specified, leave blank (defaults to 1 hour). |
| `description` | String | No | LLM Prompt | Optional agenda or notes for the event. Only include if user provided one. |
| `location` | String | No | LLM Prompt | Optional location (e.g., "Office", "Google Meet"). Only include if user mentioned one. |
| `attendees` | Array | No | LLM Prompt | List of attendee email addresses. Only include if user mentioned inviting people. |

**Example Response:**
```json
{
  "event_id": "abc123xyz",
  "title": "Team Meeting",
  "start": "2026-04-17T10:00:00-04:00",
  "end": "2026-04-17T11:00:00-04:00",
  "link": "https://www.google.com/calendar/event?eid=...",
  "message": "Event 'Team Meeting' has been created on your calendar."
}
```

---

### Tool 3: Get Recent Emails

| Field | Value |
|-------|-------|
| **Name** | `get_recent_emails` |
| **Description** | Retrieves recent emails from the user's Gmail inbox. Use when the user asks to check email, see messages, or asks what's in their inbox. |
| **Method** | `POST` |
| **URL** | `https://personal-assistant-3xh0.onrender.com/api/v1/tools/get_recent_emails` |
| **Header** | `Content-Type: application/json` |

**Body Parameters:**

| Identifier | Data Type | Required | Value Type | Description |
|-----------|-----------|----------|------------|-------------|
| `max_results` | Integer | No | LLM Prompt | Number of emails to return. Default is 5. Use 5 for "recent emails" and up to 10 for "all messages". |
| `query` | String | No | LLM Prompt | Gmail search query. Use "is:unread" for unread emails, "from:boss@example.com" for emails from someone. Leave blank for latest inbox emails. |

**Example Response:**
```json
{
  "count": 3,
  "emails": [
    {
      "from": "Alice <alice@company.com>",
      "subject": "Project Update",
      "date": "Thu, 16 Apr 2026 09:30:00 +0530",
      "snippet": "Hi, just wanted to share the latest update on the project..."
    }
  ]
}
```

---

### Tool 4: Send Email

| Field | Value |
|-------|-------|
| **Name** | `send_email` |
| **Description** | Sends an email from the user's Gmail account. ONLY call this after you have confirmed the recipient, subject, AND the full email body with the user. Always read back what you will send before calling this tool. |
| **Method** | `POST` |
| **URL** | `https://personal-assistant-3xh0.onrender.com/api/v1/tools/send_email` |
| **Header** | `Content-Type: application/json` |

**Body Parameters:**

| Identifier | Data Type | Required | Value Type | Description |
|-----------|-----------|----------|------------|-------------|
| `to` | String | Yes | LLM Prompt | Recipient's email address. Extract from what the user said (e.g., "send an email to alice@company.com"). |
| `subject` | String | Yes | LLM Prompt | Email subject line. Ask the user for this if they haven't provided it. |
| `body` | String | Yes | LLM Prompt | The full email body/content. ALWAYS ask the user "What would you like to say in the email?" if they haven't provided the content yet. Compose it naturally based on what they say. |
| `cc` | String | No | LLM Prompt | CC email address. Only include if the user asked to CC someone. |

**Example Response:**
```json
{
  "success": true,
  "message_id": "msg_abc123",
  "message": "Email successfully sent to alice@company.com with subject 'Project Update'."
}
```

---

## Step 6: Agent Conversation Examples

### Example 1 — Check Calendar
> User: "What do I have on my calendar tomorrow?"
> 
> Agent calls `get_upcoming_events` → reads back the events in plain language.
> 
> Agent: "You have two things tomorrow. A team standup at 10 AM and a dentist appointment at 2 PM."

### Example 2 — Schedule a Meeting
> User: "Add a meeting with John tomorrow at 3 PM"
> 
> Agent: "I'll schedule a meeting with John tomorrow at 3 PM. Should I call it 'Meeting with John', and set it for 1 hour?"
> 
> User: "Yes"
> 
> Agent calls `create_calendar_event` with title="Meeting with John", start="2026-04-17T15:00:00-04:00"
> 
> Agent: "Done! I've added Meeting with John to your calendar for tomorrow at 3 PM."

### Example 3 — Check Emails
> User: "Any new emails?"
> 
> Agent calls `get_recent_emails` with query="is:unread"
> 
> Agent: "You have 3 unread emails. One from Alice about a project update, one from your bank with a statement, and one from Rahul with subject 'Quick question'."

### Example 4 — Send Email
> User: "Send an email to john@example.com"
> 
> Agent: "Sure! What's the subject?"
> 
> User: "Meeting tomorrow"
> 
> Agent: "What would you like to say in the email?"
> 
> User: "Hi John, just a reminder about our meeting tomorrow at 3 PM. See you then."
> 
> Agent: "I'll send this to john@example.com with subject 'Meeting tomorrow' and body: 'Hi John, just a reminder about our meeting tomorrow at 3 PM. See you then.' Shall I send it?"
> 
> User: "Yes"
> 
> Agent calls `send_email` → confirms success.
> 
> Agent: "Done! Your email has been sent to John."

---

## Step 7: Test the Agent

1. Visit `https://personal-assistant-3xh0.onrender.com/api/v1/auth/status` to confirm your Google account is connected
2. Test in the ElevenLabs playground
3. Try these phrases:
   - "What's on my calendar this week?"
   - "Schedule a team meeting tomorrow at 10 AM"
   - "Check my emails"
   - "Send an email to [someone]"

---

## Troubleshooting

### "Not authorized" error
- Visit `https://personal-assistant-3xh0.onrender.com/api/v1/auth/google` and re-authorize
- Check that `GOOGLE_CREDENTIALS_JSON` env var is set correctly on Render

### Token expired
- The token auto-refreshes as long as `refresh_token` is present
- If it fails, re-authorize via `/api/v1/auth/google`

### Tools not being called
- Verify the tool URLs are correct and the server is running
- Check the tool descriptions — they guide the LLM on when to use each tool
- Make sure `Content-Type: application/json` header is set

### API errors in Google Cloud
- Confirm both **Google Calendar API** and **Gmail API** are enabled in your project
- Check the OAuth consent screen is configured (can be "External" for personal use)

---

## Step 7.5: Create a Fresh Google Cloud Project for `<owner-gmail>`

Start from scratch: a brand-new GCP project, a new OAuth app, and a token bound to the new Gmail account. Do all of this **while signed in to `<owner-gmail>`** in your browser — it's the owner of the project and also the account Nova will read/write.

### Step A — Sign in as the new account

1. Open a fresh Chrome profile or an incognito window.
2. Sign in to [https://accounts.google.com](https://accounts.google.com) with **`<owner-gmail>`** and that password.
3. Keep this browser session open for the rest of Step 7.5. If you accidentally log into a different Google account at any point, everything below will go to the wrong project.

### Step B — Create the Google Cloud project

1. Go to [Google Cloud Console → Project Selector](https://console.cloud.google.com/projectselector2/home/dashboard).
2. Click **New Project** (top-right).
3. Fill in:
   - **Project name**: `<your-service-name>`
   - **Organization**: No organization (personal Gmail accounts don't have one)
   - **Location**: No organization
4. Click **Create**. Wait ~20 seconds, then switch the active project to `<your-service-name>` in the top bar.

### Step C — Enable the required APIs

1. In the new project, go to **APIs & Services → Library**.
2. Search for and **Enable** each of these:
   - **Google Calendar API**
   - **Gmail API**
3. You should now see both listed under **APIs & Services → Enabled APIs & services**.

### Step D — Configure the OAuth consent screen

1. **APIs & Services → OAuth consent screen**.
2. User type: **External**. Click **Create**.
3. **App information** page:
   - **App name**: `Nova Personal Assistant`
   - **User support email**: `<owner-gmail>`
   - **Developer contact email**: `<owner-gmail>`
   - Leave logo, app domain, etc. blank.
   - Click **Save and Continue**.
4. **Scopes** page:
   - Click **Add or Remove Scopes**.
   - Select these (use the filter box to find each quickly):
     - `.../auth/calendar` — "See, edit, share, and permanently delete all the calendars you can access using Google Calendar"
     - `.../auth/calendar.events`
     - `.../auth/gmail.readonly`
     - `.../auth/gmail.send`
     - `.../auth/gmail.modify` (optional — lets Nova mark emails read later)
   - Click **Update**, then **Save and Continue**.
5. **Test users** page:
   - Click **Add Users** → enter `<owner-gmail>` → Add.
   - (Add any other Gmail addresses that also need to authorize — usually just the one.)
   - Click **Save and Continue**.
6. **Summary** page → click **Back to Dashboard**. App is in "Testing" mode. That's fine — refresh tokens for test users last ~7 days, which is plenty. If you ever need to stop re-authorizing, you can submit for verification later.

### Step E — Create the OAuth client ID

1. **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
2. **Application type**: **Web application**.
3. **Name**: `<your-service-name>-backend-web`.
4. **Authorized redirect URIs** — add both:
   - `http://localhost:8000/api/v1/auth/google/callback` (for local dev)
   - `https://personal-assistant-3xh0.onrender.com/api/v1/auth/google/callback` (for production — **replace with your Render URL if different**)
5. Click **Create**.
6. A modal pops up with client ID + secret — click **Download JSON**. Save the file as `credentials.json`.

### Step F — Turn `credentials.json` into the `GOOGLE_CREDENTIALS_JSON` env var

Open `credentials.json` in a text editor. It looks like:
```json
{ "web": { "client_id": "...", "client_secret": "...", "redirect_uris": [...], ... } }
```

The env var value is **the entire file contents as one line**. You can minify it with:
```bash
python -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1]))))" credentials.json
```
Copy the resulting single-line JSON.

### Step G — Set env vars on Render

In the Render dashboard → your web service → **Environment**:

| Key | Value |
|-----|-------|
| `GOOGLE_CREDENTIALS_JSON` | the minified JSON from Step F |
| `GOOGLE_TOKEN_JSON` | (leave blank for now — we'll fill this in Step I) |
| `OWNER_EMAIL` | `<owner-gmail>` |

Click **Save Changes**. Render will redeploy.

### Step H — Authorize the backend as `<owner-gmail>`

1. Still in the same incognito/fresh-profile browser logged in as `<owner-gmail>`, visit:
   ```
   https://personal-assistant-3xh0.onrender.com/api/v1/auth/google
   ```
2. Google's consent screen appears. Confirm the email shown at the top is `<owner-gmail>`. If it's not, click the avatar → "Use another account" → pick the right one.
3. Google warns "Google hasn't verified this app" — click **Advanced → Go to Nova Personal Assistant (unsafe)**. (This warning is normal for apps in Testing mode; it's YOUR app.)
4. Grant all requested scopes.
5. You should land on a "✓ Google Account Connected!" page.

### Step I — Persist the token so Render restarts don't lose it

Render's free tier has ephemeral disk. If the service restarts, `token.json` on disk is wiped. Copy it to an env var so it persists:

1. In the same browser, open:
   ```
   https://personal-assistant-3xh0.onrender.com/api/v1/auth/token
   ```
2. Copy the JSON body (it's the full token with `refresh_token`, `client_id`, etc).
3. Render dashboard → **Environment** → set **`GOOGLE_TOKEN_JSON`** to that JSON string → **Save Changes**.
4. Render redeploys. Once it's back up, tokens will refresh automatically for ~7 days (longer if you submit the app for verification).

### Step J — Verify end-to-end

```bash
# 1. Auth healthy?
curl https://personal-assistant-3xh0.onrender.com/api/v1/auth/status
# → {"authorized": true, ...}

# 2. Gmail reads work?
curl -X POST -H "Content-Type: application/json" \
  -d '{"max_results":3,"query":"is:unread"}' \
  https://personal-assistant-3xh0.onrender.com/api/v1/tools/get_recent_emails
# → {"emails":[...], "count":...}  (emails from <owner-gmail>)

# 3. Calendar reads work?
curl -X POST -H "Content-Type: application/json" \
  -d '{"max_results":5}' \
  https://personal-assistant-3xh0.onrender.com/api/v1/tools/get_upcoming_events
```

### Common gotchas

- **"Error 403: access_denied"** on the consent screen → `<owner-gmail>` wasn't added as a test user in Step D.5. Add it and retry.
- **"redirect_uri_mismatch"** → the redirect URI on the OAuth client doesn't exactly match the backend's URL. Double-check it includes `/api/v1/auth/google/callback` (note the `/api/v1/` prefix).
- **Gmail calls return messages from the old account** → you're still using an old `GOOGLE_TOKEN_JSON`. Blank it out, redeploy, and re-authorize while logged in as the new account.
- **Tokens stop working after ~7 days** → that's the Testing-mode limit on refresh tokens. Either re-authorize weekly, or submit the consent screen for verification (free, takes a few days).

---

## Step 8.7: OpenAI Setup (Optional — Natural-Language Summaries)

OpenAI is used for two things inside the daily 7 AM briefing:
1. **SMS digest** — pulls **all** unread emails (up to 25) and summarizes them into a short, human-readable blurb instead of raw sender/subject bullets.
2. **Voice script** — writes the outbound call's opening line as natural flowing speech (no robotic lists).

Everything still works without OpenAI — the code falls back to deterministic templates. But with it, the briefing feels much more like a real assistant.

1. Get an API key from [OpenAI Platform → API Keys](https://platform.openai.com/api-keys).
2. On Render, add:

| Variable | Value |
|----------|-------|
| `OPENAI_API_KEY` | `sk-proj-...` |
| `OPENAI_MODEL` | `gpt-4o-mini` (default — cheap and fast, plenty for this use case) |

3. Redeploy. `GET /api/v1/cron/health` will show `"openai_configured": true`.

> Cost estimate: each 7 AM briefing uses ~500 tokens total → well under $0.001/day with `gpt-4o-mini`.

---

## Production Hardening Checklist

Already in the code — just verify in Render:

- [ ] `CRON_SECRET` is a strong random value (`openssl rand -hex 32`). Only needed for manual `/cron/*` triggers.
- [ ] `TWILIO_AUTH_TOKEN` has NOT been shared in chat logs / screenshots / commits. If it was, rotate it in the Twilio console.
- [ ] `GOOGLE_TOKEN_JSON` is set on Render (otherwise Render's ephemeral disk loses it on every restart and you'll see 401s).
- [ ] `.env` and `token.json` are in `.gitignore` (they are — double-check before pushing).
- [ ] Google Cloud → OAuth consent screen → **Publishing status** — while "Testing", refresh tokens expire every 7 days. For a production assistant, submit for verification or keep it in testing and just re-auth weekly.
- [ ] Twilio phone number has SMS capability enabled and (in the US) is registered for A2P 10DLC if sending to US numbers regularly — unregistered numbers get throttled or blocked.
- [ ] **Scheduler reliability**: Render service is on **Starter plan** (always-on) OR you have an uptime monitor pinging `/health` every 5 min to keep the free tier awake.
- [ ] Gunicorn is running with `-w 1` (confirmed in `render.yaml`). Multiple workers would duplicate every scheduled job.
- [ ] `GET /scheduler` shows `"running": true` with both jobs listed and a valid `next_run`.
- [ ] All secrets set via `sync: false` in `render.yaml` (they never appear in git).

---

## Step 8: Twilio + Scheduled Notifications Setup

This section sets up:
- SMS alerts to {OWNER_NAME} when unknown callers interact
- 7:00 AM daily briefing (SMS + automated phone call)
- 30-minute pre-meeting reminders via SMS

### 8.1 — Twilio account

1. Create/log into your [Twilio Console](https://console.twilio.com)
2. Buy a Twilio phone number (SMS-capable)
3. Grab from the Twilio dashboard:
   - **Account SID**
   - **Auth Token**
   - **Twilio phone number** (the "from" number, E.164 format e.g. `+14155550123`)

### 8.2 — Backend env vars (add to Render)

| Variable | Value |
|----------|-------|
| `OWNER_PHONE_NUMBER` | `<owner-phone-e164>` ({OWNER_NAME}'s number — already default in config) |
| `OWNER_NAME` | `{OWNER_NAME}` |
| `TWILIO_ACCOUNT_SID` | From Twilio console |
| `TWILIO_AUTH_TOKEN` | From Twilio console |
| `TWILIO_PHONE_NUMBER` | Your Twilio number, e.g. `+14155550123` |
| `CRON_SECRET` | Any random string (used as shared secret for cron endpoints). Generate with `openssl rand -hex 32` |

### 8.3 — ElevenLabs outbound call (for the 7 AM morning call)

The daily 7 AM call uses ElevenLabs' outbound-call API, which places the call through Twilio:

1. In ElevenLabs dashboard → **Conversational AI** → **Phone Numbers** → link the Twilio number you purchased
2. Copy the **Phone Number ID** ElevenLabs assigns (shown in the phone numbers list)
3. Create/copy an **API key** from ElevenLabs (Profile → API Keys)
4. Add these env vars to Render:

| Variable | Value |
|----------|-------|
| `ELEVENLABS_API_KEY` | Your ElevenLabs API key |
| `ELEVENLABS_AGENT_ID` | Your Nova agent ID (from the agent's URL in ElevenLabs) |
| `ELEVENLABS_AGENT_PHONE_NUMBER_ID` | The phone number ID from step 2 |
| `DATABASE_URL` | Render PostgreSQL connection string (enables contacts, call logs, messages, notes, sentiment, and the dashboard) |

### 8.4 — Scheduling: In-Process (Default)

The backend now ships with a **built-in scheduler** (APScheduler) that runs both jobs automatically — no external cron service required.

**What runs automatically:**
| Job | Schedule | What it does |
|-----|----------|--------------|
| `daily_summary` | Every day at **7:00 AM Eastern** (DST-aware) | Sends SMS briefing + triggers an ElevenLabs outbound call reading today's meetings and **all** unread emails (OpenAI-summarized when configured) |
| `meeting_reminders` | Every **10 minutes** | Finds any event starting in ~30 min and SMS-es a reminder |

**Config (default — already set):**
```
ENABLE_SCHEDULER=true
TIMEZONE=America/New_York
```

**⚠ Requirements for the scheduler to reliably fire at 7 AM:**

1. **Single worker.** Already set in `render.yaml` via `gunicorn -w 1`. Don't increase this — multiple workers = duplicate SMS.
2. **Service must be awake.** Render's free tier sleeps after ~15 min of no HTTP traffic. If the service is asleep at 7 AM, the scheduler is also asleep.

Two options to keep it reliable:

| Option | Cost | Reliability |
|--------|------|-------------|
| **A. Render Starter plan** — service never sleeps | $7/mo | ⭐⭐⭐ Best |
| **B. Free tier + external ping** — UptimeRobot / BetterStack hits `/health` every 5 min | Free | ⭐⭐ Good, occasional cold starts |
| **C. Fall back to Render Cron Jobs** — external UTC-scheduled curl calls to `/cron/*` (see 8.4-alt below) | ~$0 | ⭐⭐⭐ Works on free tier |

### 8.4-alt — Fallback: External Render Cron Jobs

If you want to stay on free tier without an uptime monitor, set `ENABLE_SCHEDULER=false` in the web service env vars, then create two Render Cron Jobs that hit the HTTP endpoints instead:

**Daily briefing (DST-safe via dual-schedule + local-hour guard):**

| Field | Value |
|-------|-------|
| Name | `<your-service-name>-daily-summary` |
| Schedule | `0 11,12 * * *` (covers both EDT 11:00 UTC and EST 12:00 UTC) |
| Command | `curl -f -X POST -H "X-Cron-Secret: $CRON_SECRET" https://personal-assistant-3xh0.onrender.com/api/v1/cron/daily_summary` |

The endpoint returns `{"skipped": true, ...}` for the wrong-offset hit (expected).

**Meeting reminders:**

| Field | Value |
|-------|-------|
| Name | `<your-service-name>-meeting-reminders` |
| Schedule | `*/10 * * * *` |
| Command | `curl -f -X POST -H "X-Cron-Secret: $CRON_SECRET" https://personal-assistant-3xh0.onrender.com/api/v1/cron/meeting_reminders` |

Both cron jobs need `CRON_SECRET` set to the same value as the web service.

### 8.5 — Inspecting & Testing Scheduled Jobs

**Check the live scheduler state (no secret required):**

```bash
curl https://personal-assistant-3xh0.onrender.com/scheduler
```

Example response:
```json
{
  "running": true,
  "timezone": "America/New_York",
  "jobs": [
    {"id": "daily_summary", "next_run": "2026-04-21T07:00:00-04:00", "trigger": "cron[hour='7', minute='0']"},
    {"id": "meeting_reminders", "next_run": "2026-04-20T14:30:00-04:00", "trigger": "cron[minute='*/10']"}
  ]
}
```

**Full system health:**
```bash
curl https://personal-assistant-3xh0.onrender.com/api/v1/cron/health
```

**Manually trigger either job right now (requires `CRON_SECRET`):**
```bash
# Force the daily briefing immediately (bypasses 7 AM guard)
curl -X POST -H "X-Cron-Secret: YOUR_SECRET" \
  "https://personal-assistant-3xh0.onrender.com/api/v1/cron/daily_summary?force=true"

# Run meeting reminders now
curl -X POST -H "X-Cron-Secret: YOUR_SECRET" \
  https://personal-assistant-3xh0.onrender.com/api/v1/cron/meeting_reminders
```

### 8.6 — How each piece works

| Trigger | Component | What happens |
|---------|-----------|--------------|
| Unknown caller talks to agent | `validate_caller` → `notify_owner` | Agent declines actions, collects message, SMSes {OWNER_NAME} |
| Every day at 7:00 AM Eastern | In-process scheduler → `jobs.run_daily_summary()` | SMS summary + outbound phone call (Nova reads today's meetings + all unread emails, OpenAI-summarized when available) |
| Every 10 min | In-process scheduler → `jobs.run_meeting_reminders()` | Checks calendar for meetings starting in ~30 min; SMS-es a reminder |
| Any time during conversation | Agent calls `notify_owner` | Extra SMS to {OWNER_NAME} with whatever context the agent chooses |
