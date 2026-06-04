# Test Playbook — InpharmD MI Inquiry App

End-to-end tests you can run against the deployed services without disturbing the 90 real US manufacturers. All tests target the `Yanthraa (TEST)` manufacturer (id 92).


| Service         | URL                                                                                              |
| --------------- | ------------------------------------------------------------------------------------------------ |
| **Web app**     | [https://inpharmd-inquiry-web.onrender.com](https://inpharmd-inquiry-web.onrender.com)           |
| **Backend API** | [https://inpharmd-inquiry-api.onrender.com](https://inpharmd-inquiry-api.onrender.com)           |
| **API docs**    | [https://inpharmd-inquiry-api.onrender.com/docs](https://inpharmd-inquiry-api.onrender.com/docs) |



| Test manufacturer | Value                                               |
| ----------------- | --------------------------------------------------- |
| Name              | Yanthraa (TEST)                                     |
| ID                | 92                                                  |
| MI Email          | [praveen@yanthraa.com](mailto:praveen@yanthraa.com) |
| MI Phone          | +91 9848639655                                      |
| Hours             | Mon-Sat 9a-9p IST                                   |


---

## Example Inquiry Questions

Copy any of these into the form to simulate real pharmacist queries. They're realistic enough to test the voice-agent prompt and the email template.

The set is intentionally a **mix of simple and multi-part questions** so you can verify both flows on the same agent:

- **Single question** (#1, #3, #6) — checks the basic ask → answer → submit flow
- **Two-part** (#2, #4) — checks that the agent asks the second part only AFTER the rep finishes the first
- **Three-part** (#5, #7, #8) — checks the full "one question at a time, take a beat, then ask the next" pacing from the system prompt

Each example also includes a **Sample Answer** you can read aloud when the voice agent calls you. For multi-part questions the answer is split per sub-question — say each chunk only after the agent asks that specific part, so you can verify the agent isn't dumping everything at once.

> ⚠ The sample answers below are **fictional test scripts**. Case IDs, study names, and percentages are made up for testing purposes only. Do not use them as real medical guidance.

### 1. Stability / Temperature Excursion — Humira (adalimumab)  *(single question)*

**Subject:** `Stability data for Humira after temperature excursion`

**Question:**

> Is Humira (adalimumab 40 mg pre-filled syringe) still safe to dispense after a 6-hour temperature excursion at around 30 °C during last-mile delivery?

**Sample Answer (read aloud during the test call):**

> Yes — Humira retains potency for up to 14 days at room temperature, up to 25 degrees Celsius, when stored in its original carton. A 6-hour excursion at 30 degrees is outside the labeled handling, but our internal study shows no measurable impact for brief exposures under 8 hours. Safe to dispense if the patient uses it within the next 14 days. Reference: AbbVie stability study H-U-2023-S-T-B-018.

### 2. Y-Site Compatibility — Protonix + vancomycin  *(two-part)*

**Subject:** `Y-site compatibility — Protonix (pantoprazole) with vancomycin in 0.9% NaCl`

**Question:**

> 1. Is Protonix (pantoprazole) compatible with vancomycin at Y-site in 0.9% normal saline?
> 2. If they're not compatible, what should we do for an ICU patient where we're trying to minimize line access?

**Sample Answer (read aloud during the test call — split per sub-question):**

> **Part 1:** No, they're physically incompatible. A white precipitate forms within about 15 minutes because of the pH mismatch — Protonix is alkaline and vancomycin is acidic. Reference: Pfizer Compatibility Database, section 4.2.
>
> **Part 2:** We recommend using separate infusion lines. If a single line is truly unavoidable, flush thoroughly between administrations with at least 20 mL of saline — but separate lines are strongly preferred.

### 3. Renal Dosing — Eliquis (apixaban)  *(single question)*

**Subject:** `Eliquis (apixaban) dosing in CrCl 22 mL/min`

**Question:**

> What's the recommended Eliquis (apixaban) dose for atrial fibrillation in a 68-year-old patient with CrCl of 22 mL/min and weight 74 kg?

**Sample Answer (read aloud during the test call):**

> The recommended dose is 5 milligrams twice daily — the standard dose. Dose reduction to 2.5 milligrams twice daily only applies if the patient meets at least two of the three reduction criteria — age 80 or older, weight 60 kilograms or less, or serum creatinine 1.5 or higher. Your patient meets only the renal criterion, so they stay on the standard dose. Reference: Eliquis PI section 2.1.

### 4. Off-Label Pediatric Use — Prograf (tacrolimus)  *(two-part)*

**Subject:** `Off-label pediatric use of Prograf — 9-year-old, 28 kg post-transplant`

**Question:**

> 1. Does the manufacturer have pediatric data on opening Prograf (tacrolimus) IR capsules and sprinkling them for a 9-year-old, 28 kg liver transplant patient?
> 2. If opened capsules aren't recommended, is there a granule formulation we should be ordering instead?

**Sample Answer (read aloud during the test call — split per sub-question):**

> **Part 1:** Opening the IR capsules is off-label, but we do have a 2019 case series of 23 pediatric liver transplant patients where capsule contents were mixed with apple sauce. Trough levels were comparable to the granule formulation when adjusted for weight. If you must go that route, monitor whole-blood troughs twice weekly for the first month. Reference: Astellas Medical Information case series M-I-C-2019-T-042.
>
> **Part 2:** Yes — there's an FDA-approved Prograf granules formulation specifically for pediatric use. That's what we'd recommend ordering. Your wholesaler should be able to source it.

### 5. Drug Interaction — Pacerone (amiodarone) + Coumadin (warfarin)  *(three-part)*

**Subject:** `Interaction — initiating Pacerone in patient on stable Coumadin`

**Question:**

> A 78-year-old patient is on Coumadin (warfarin) 5 mg daily with a stable INR around 2.4. We're starting Pacerone (amiodarone) 200 mg daily for new-onset atrial fibrillation. Three questions:
>
> 1. What's the typical magnitude of INR elevation we should expect?
> 2. How soon after starting amiodarone do we usually see the peak effect?
> 3. Does the manufacturer recommend a pre-emptive warfarin dose reduction, or just more frequent INR monitoring?

**Sample Answer (read aloud during the test call — split per sub-question):**

> **Part 1:** The expected INR rise is in the range of 30 to 50 percent above baseline. Amiodarone inhibits CYP2C9 and CYP3A4, which slows warfarin metabolism.
>
> **Part 2:** The peak effect typically shows up between one and three weeks after starting amiodarone — it's not immediate.
>
> **Part 3:** We recommend a pre-emptive reduction of the warfarin dose by 30 to 50 percent at amiodarone initiation, plus weekly INR monitoring for the first 4 to 6 weeks, then every 2 weeks until stable. Reference: Pacerone prescribing information, section 7.5.

### 6. Reconstitution / Compounding — Zosyn (piperacillin-tazobactam)  *(single question)*

**Subject:** `Zosyn beyond-use date in elastomeric pump for ambulatory OPAT`

**Question:**

> Is Zosyn (piperacillin-tazobactam) 3.375 g stable in an elastomeric infusion pump for 24 hours at body-adjacent temperature, around 32 °C, for ambulatory OPAT?

**Sample Answer (read aloud during the test call):**

> Yes — Zosyn 3.375 grams in 100 milliliters of normal saline is stable for up to 24 hours in elastomeric pumps at body-adjacent temperatures up to 32 degrees Celsius. Both potency and sterility are maintained. For runs longer than 24 hours, we'd recommend refrigerated storage between doses. Reference: Pfizer stability study Z-O-S-2022-014.

### 7. Therapeutic Substitution — Synthroid → generic levothyroxine  *(three-part)*

**Subject:** `Switching from Synthroid to generic levothyroxine — NTI concern`

**Question:**

> A Hashimoto's patient stable on brand Synthroid 125 mcg daily is being switched to AB-rated generic levothyroxine. Three things I'd like to confirm:
>
> 1. Does the manufacturer have case data showing clinically meaningful TSH fluctuation after a brand-to-generic switch?
> 2. If so, roughly what percentage of patients end up outside their target TSH range?
> 3. How soon after the switch should we recheck TSH?

**Sample Answer (read aloud during the test call — split per sub-question):**

> **Part 1:** Yes — even though AB-rated generics meet bioequivalence standards, we have documented TSH fluctuation post-switch. In a 2021 case series we sponsored, 156 stable hypothyroid patients were followed for 6 months. Reference: AbbVie Synthroid case series S-Y-N-2021-C-S-008.
>
> **Part 2:** About 12 percent of patients had TSH shifts outside their target range within 3 months. For Hashimoto's patients specifically, the rate was slightly higher at about 15 percent.
>
> **Part 3:** We recommend rechecking TSH 6 to 8 weeks after the switch and adjusting the dose if needed.

### 8. Adverse Event Follow-up — Lamictal + Depakote rash  *(three-part)*

**Subject:** `Reported adverse event — patient on Lamictal and Depakote`

**Question:**

> A patient on day 11 of Lamictal (lamotrigine), also taking stable Depakote (valproate), developed a maculopapular rash on the trunk only — no mucosal involvement, no fever, no eosinophilia. Three questions:
>
> 1. Should we hold the Lamictal, or can we keep going since the rash looks benign?
> 2. If we hold it and the rash resolves, can we attempt re-titration?
> 3. If we can re-titrate, what starting dose and titration speed do you recommend on concomitant valproate?

**Sample Answer (read aloud during the test call — split per sub-question):**

> **Part 1:** Hold the Lamictal. Valproate inhibits glucuronidation and roughly doubles lamotrigine concentrations, so even a benign-looking rash on concomitant valproate carries a real risk of progressing to Stevens-Johnson syndrome. Better to err on the side of caution.
>
> **Part 2:** Yes, re-titration is acceptable after the rash fully resolves — provided it was non-severe with no mucosal or systemic involvement, which yours sounds like.
>
> **Part 3:** Start at 12.5 milligrams every other day, increase every 2 weeks, and cap the maintenance dose at 100 milligrams per day while on valproate. If the rash recurs at any point, discontinue permanently. Reference: Lamictal PI section 5.1 and GSK medical information letter M-I-L-2022-L-019.

---

## Test 1 — Web UI End-to-End (Voice Agent Path)

Goal: place a real outbound call against your phone (+91 9848639655) using the deployed agent.

1. Open [https://inpharmd-inquiry-web.onrender.com/#inquiries](https://inpharmd-inquiry-web.onrender.com/#inquiries)
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
12. The agent should open: *"Hello, this is the InpharmD medical information line calling on behalf of a pharmacist with an inquiry regarding Yanthraa (TEST). I need to ask about: Stability data for Humira after temperature excursion. Is this a good time?"*
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
    - **Subject:** `[InpharmD #N] Stability data for Humira after temperature excursion`
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


| Response                            | Meaning                                                                                  |
| ----------------------------------- | ---------------------------------------------------------------------------------------- |
| `{"detail":"Inquiry 0 not found"}`  | ✓ Backend reachable, auth header valid                                                   |
| `{"detail":"Invalid agent secret"}` | ✗ Wrong secret — check `AGENT_TOOLS_SECRET` on Render matches the ElevenLabs tool header |
| `502 Bad Gateway`                   | ✗ Render service is down — check the dashboard                                           |


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

