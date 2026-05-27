# Test Playbook — InpharmD MI Inquiry App

End-to-end tests you can run against the deployed services without disturbing the 90 real US manufacturers. All tests target the `Yanthraa (TEST)` manufacturer (id 92).

| Service | URL |
|---|---|
| **Web app** | https://inpharmd-inquiry-web.onrender.com |
| **Backend API** | https://inpharmd-inquiry-api.onrender.com |
| **API docs** | https://inpharmd-inquiry-api.onrender.com/docs |

| Test manufacturer | Value |
|---|---|
| Name | Yanthraa (TEST) |
| ID | 92 |
| MI Email | praveen@yanthraa.com |
| MI Phone | +91 9848639655 |
| Hours | Mon-Sat 9a-9p IST |

---

## Example Inquiry Questions

Copy any of these into the form to simulate real pharmacist queries. They're realistic enough to test the voice-agent prompt and the email template.

### 1. Stability / Temperature Excursion

**Subject:** `Stability data for Drug X after temperature excursion`

**Question:**
> A pharmacy received a shipment of Drug X (250 mg tablets) that was exposed to ambient temperatures of approximately 30 °C for 6 hours during last-mile delivery. The package insert states 'store between 15–25 °C'. Is there stability data supporting that the product can still be dispensed after this excursion? Please reference the specific PI section or stability study if available.

### 2. Y-Site Compatibility

**Subject:** `Y-site compatibility — Drug Y with vancomycin in 0.9% NaCl`

**Question:**
> Looking for compatibility data: can Drug Y be infused via Y-site alongside vancomycin in 0.9% normal saline? Specifically need visual compatibility, pH compatibility, and any precipitation data over a 4-hour co-infusion window. Patient is in ICU and we are trying to minimize line access.

### 3. Renal Dosing

**Subject:** `Drug Z dosing adjustment in renal impairment (CrCl 22 mL/min)`

**Question:**
> Recommended dose of Drug Z for a 68-year-old patient with CrCl of 22 mL/min and stable hepatic function? The PI gives ranges for CrCl >30 and <15 but does not address 15–30 directly. Is there a published study or unpublished company data supporting dosing in this band?

### 4. Off-Label Pediatric Use

**Subject:** `Off-label pediatric use of Drug A — 9-year-old, 28 kg`

**Question:**
> The treating physician is considering Drug A off-label for a 9-year-old patient weighing 28 kg with refractory condition X. The label is adult-only. Does the manufacturer have any pediatric pharmacokinetic data, case series, or expanded-access program experience that could inform dosing?

### 5. Drug Interaction

**Subject:** `Interaction — Drug B + warfarin in elderly patient`

**Question:**
> Patient is 78 years old, on warfarin (INR target 2–3, currently stable), and Drug B is being added for a new indication. PI mentions 'may potentiate anticoagulant effect'. Looking for the magnitude of effect — what INR rise should the team anticipate, and is dose-adjustment of warfarin recommended pre-emptively or only after the next INR check?

### 6. Reconstitution / Compounding

**Subject:** `Drug C reconstitution and beyond-use date in elastomeric pump`

**Question:**
> Pharmacy wants to compound Drug C in an elastomeric infusion pump for 48-hour ambulatory use at 32 °C. The official PI gives 24h refrigerated stability post-reconstitution but no elastomeric/ambulatory data. Does the manufacturer have any in-use stability data supporting 48 h at body-adjacent temperature?

### 7. Therapeutic Substitution

**Subject:** `Switching from Drug D to its generic — bioequivalence concern`

**Question:**
> Patient has been stable on brand Drug D for 8 months. The formulary is switching to the AB-rated generic. Are there documented cases of clinical non-equivalence (specifically narrow-therapeutic-index concerns) when switching from your brand to the generic? Anything beyond the standard AB-rated bioequivalence range?

### 8. Adverse Event Follow-up

**Subject:** `Reported adverse event — patient on Drug E and Drug F`

**Question:**
> Patient initiated Drug E 14 days ago, also on stable Drug F. Developed a rash on day 11 (maculopapular, no mucosal involvement, no fever). PI lists rash as 'common'. Is there a known interaction or PK potentiation when Drug E and Drug F are co-administered that increases dermatologic risk? Patient has been re-challenged in similar circumstances before with no event.

---

## Test 1 — Web UI End-to-End (Voice Agent Path)

Goal: place a real outbound call against your phone (+91 9848639655) using the deployed agent.

1. Open https://inpharmd-inquiry-web.onrender.com/#inquiries
2. Click **+ New Inquiry**
3. Pick `Yanthraa (TEST)` from the manufacturer dropdown — you should see the manufacturer's email + phone + SLA appear underneath
4. Subject: paste from Example 1 above
5. Question: paste from Example 1 above
6. Requester Name: `Praveen Ivaturi`
7. Requester Email: `ivaturipraveen11@gmail.com`
8. Fallback: leave at `24 hours`
9. Click **Create Inquiry** → form closes, Channel Chooser opens with 2 cards
10. Click **Call Agent Now** on the right card
11. Your phone (+91 9848639655) should ring within ~5 seconds — answer it
12. The agent should open: *"Hello, this is the InpharmD medical information line calling on behalf of a pharmacist with an inquiry regarding Yanthraa (TEST). I need to ask about: Stability data for Drug X after temperature excursion. Is this a good time?"*
13. Reply *"yes"*, the agent reads the full question
14. Give a brief verbal answer (e.g. *"yes, our stability study shows 95% potency at 30°C for 72 hours, per study STAB-2024"*)
15. Agent calls `submit_answer` silently, then says goodbye
16. Refresh the inquiry detail in the UI → status should be `call_completed`, the **Final Answer** box shows your spoken answer

**Expected result:** inquiry resolved within ~3 minutes of clicking the button.

**If the call doesn't fire:** check Render API logs for the error returned by `/trigger-call`. Most common causes:
- `503 ELEVENLABS_API_KEY is not set` → env var missing on Render
- `502 ElevenLabs rejected the call` → wrong agent ID or phone number ID
- `409 out_of_hours` → Yanthraa's hours say outside business window. UI offers a "call anyway" prompt.

---

## Test 2 — Web UI End-to-End (Email Path)

Goal: send a real email from `ivaturipraveen11@gmail.com` to `praveen@yanthraa.com`.

1. Same steps 1–9 as Test 1, but **fill in Requester Email** so you can see the Reply-To behavior
2. In the Channel Chooser, click **Send Email** on the left card
3. Check `praveen@yanthraa.com` inbox within ~10 seconds — email should arrive with:
    - **From:** `InpharmD MI <ivaturipraveen11@gmail.com>`
    - **Subject:** `[InpharmD #N] Stability data for Drug X after temperature excursion`
    - **Reply-To:** `ivaturipraveen11@gmail.com` (or whatever you put in requester_email)
4. Reply to the email (keep subject intact!) with a brief answer
5. Reply lands in `ivaturipraveen11@gmail.com`
6. In the app, open the inquiry → scroll to **Log email response** panel → paste the reply text → **Save Email Response**
7. Inquiry status flips to `email_responded`, the answer shows in the Final Answer box

**If the email doesn't arrive:**
- Check Gmail's "Sent" folder of `ivaturipraveen11@gmail.com` — if it's there, the issue is delivery (check spam folder on receive)
- Check Render API response: a `503` means SMTP env vars missing on Render; `502` means the SMTP auth or send failed (App Password likely wrong)

---

## Test 3 — API Smoke Test (no UI needed)

Useful for verifying the backend after a config change.

```bash
BASE=https://inpharmd-inquiry-api.onrender.com

# 1. Health check
curl -s "$BASE/health"
# → {"status":"ok"}

# 2. Manufacturer count (should be ≥ 91)
curl -s "$BASE/api/manufacturers" | python3 -c "import json,sys; print(len(json.load(sys.stdin)),'manufacturers')"

# 3. Create a test inquiry against Yanthraa (id 92)
INQ=$(curl -s -X POST "$BASE/api/inquiries" \
  -H "Content-Type: application/json" \
  -d '{
    "manufacturer_id": 92,
    "subject": "API smoke test",
    "question": "Verifying create endpoint works.",
    "requester_name": "API Test",
    "requester_email": "ivaturipraveen11@gmail.com",
    "fallback_after_hours": 24
  }' | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['id'])")
echo "Created inquiry $INQ"

# 4. Trigger email send (skip this if you don't want a real email)
curl -s -X POST "$BASE/api/inquiries/$INQ/send-email" | python3 -m json.tool | head -10

# 5. Trigger voice call (skip if you don't want your phone to ring)
# curl -s -X POST "$BASE/api/inquiries/$INQ/trigger-call" | python3 -m json.tool | head -10

# 6. Clean up
curl -s -X DELETE "$BASE/api/inquiries/$INQ" -o /dev/null -w "Cleanup: %{http_code}\n"
```

---

## Test 4 — Voice Agent Tool Reachability

Verifies the agent's `submit_answer` tool can hit your backend.

```bash
BASE=https://inpharmd-inquiry-api.onrender.com
SECRET=236ccc970bb6801ed02707c612aef0da49fb4deea5adfe25c5892759b5dc70ee   # AGENT_TOOLS_SECRET

# Should return "Inquiry 0 not found" (means auth + routing both work)
curl -s -X POST "$BASE/api/agent-tools/submit-answer" \
  -H "Content-Type: application/json" \
  -H "X-Agent-Secret: $SECRET" \
  -d '{"inquiry_id":0,"outcome":"answered","answer":"reachability test"}'
```

| Response | Meaning |
|---|---|
| `{"detail":"Inquiry 0 not found"}` | ✓ Backend reachable, auth header valid |
| `{"detail":"Invalid agent secret"}` | ✗ Wrong secret — check `AGENT_TOOLS_SECRET` on Render matches the ElevenLabs tool header |
| `502 Bad Gateway` | ✗ Render service is down — check the dashboard |

---

## Test 5 — Business-Hours Guard

Verifies the parser correctly identifies Yanthraa as in-hours during IST business hours.

```bash
curl -s -X POST https://inpharmd-inquiry-api.onrender.com/api/inquiries/{N}/business-hours
# (replace {N} with any existing inquiry id for Yanthraa)
```

Response:
```json
{
  "known": true,
  "in_hours": true,         // true during 9a-9p IST, Mon-Sat
  "hours_text": "Mon-Sat 9a-9p IST",
  "phone": "+919848639655"
}
```

---

## Cleaning up test inquiries

Every test inquiry leaves a row in the DB. To clean up:

```bash
BASE=https://inpharmd-inquiry-api.onrender.com
# List Yanthraa inquiries
curl -s "$BASE/api/inquiries?manufacturer_id=92" | python3 -c "
import json, sys
for i in json.load(sys.stdin):
    print(i['id'], i['status'], i['subject'])
"
# Delete a specific one
curl -X DELETE "$BASE/api/inquiries/<id>"
```

Or just leave them — the dashboard handles dozens fine.

---

## What to do when something breaks

1. **Render API logs:** dashboard → `inpharmd-inquiry-api` → **Logs** tab. Stream is live; you can see every request and exception.
2. **ElevenLabs call log:** dashboard → Conversational AI → Agents → `InpharmD MI Inquiry` → **Calls** tab. Shows every outbound call, transcript, and tool calls.
3. **Render web logs:** dashboard → `inpharmd-inquiry-web` → **Logs**. Mostly just build output.
4. **DB direct query:** the Postgres external URL works from your laptop:
   ```bash
   psql "postgresql://inpharmdassistant:5z…@dpg-d8b5qgcm0tmc73d89hg0-a.oregon-postgres.render.com/inpharmd"
   ```
   Then `SELECT id, status, subject, call_provider_status FROM inquiries ORDER BY id DESC LIMIT 5;`
