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

Each example also includes a **Sample Answer** that you can read aloud when the voice agent calls you — that way the agent captures a realistic-sounding response and you can verify the end-to-end flow (transcript → `submit_answer` → "Final Answer" card → AI extraction if needed).

> ⚠ The sample answers below are **fictional test scripts**. Case IDs, study names, and percentages are made up for testing purposes only. Do not use them as real medical guidance.

### 1. Stability / Temperature Excursion — Humira (adalimumab)

**Subject:** `Stability data for Humira after temperature excursion`

**Question:**

> A pharmacy received a shipment of Humira (adalimumab 40 mg/0.4 mL pre-filled syringes) that was exposed to ambient temperatures of approximately 30 °C for 6 hours during last-mile delivery. The package insert states 'store refrigerated at 36–46 °F (2–8 °C); do not freeze'. Is there stability data supporting that the product can still be dispensed after this excursion? Please reference the specific PI section or stability study if available.

**Sample Answer (read aloud during the test call):**

> Based on our stability data, Humira retains potency for up to 14 days at room temperature, up to 25 degrees Celsius, when stored in its original carton. A 6-hour excursion at 30 degrees is outside the labeled handling but our internal study shows no measurable impact for brief exposures under 8 hours. We recommend dispensing the product if the patient will use it within the next 14 days. Reference: AbbVie stability study number H-U-2023-S-T-B-zero-one-eight.

### 2. Y-Site Compatibility — Protonix + vancomycin

**Subject:** `Y-site compatibility — Protonix (pantoprazole) with vancomycin in 0.9% NaCl`

**Question:**

> Looking for compatibility data: can Protonix (pantoprazole) be infused via Y-site alongside vancomycin in 0.9% normal saline? Specifically need visual compatibility, pH compatibility, and any precipitation data over a 4-hour co-infusion window. Patient is in ICU and we are trying to minimize line access.

**Sample Answer (read aloud during the test call):**

> Per our compatibility database, Protonix and vancomycin are physically incompatible at Y-site in normal saline. A white precipitate forms within about 15 minutes due to the pH mismatch — Protonix is alkaline, vancomycin is acidic. We recommend using separate infusion lines, or if a single line is unavoidable, flushing thoroughly between administrations with at least 20 mL of saline. Reference: Pfizer Compatibility Database, section 4.2.

### 3. Renal Dosing — Eliquis (apixaban)

**Subject:** `Eliquis (apixaban) dosing in CrCl 22 mL/min`

**Question:**

> Recommended dose of Eliquis (apixaban) for atrial fibrillation in a 68-year-old patient with CrCl of 22 mL/min, weight 74 kg, and stable hepatic function? The PI lists dose-reduction criteria (age ≥80, weight ≤60 kg, SCr ≥1.5) but our patient meets only the renal criterion. Is there a published study or unpublished company data supporting dosing in the CrCl 15–29 mL/min band?

**Sample Answer (read aloud during the test call):**

> For your patient with CrCl of 22 milliliters per minute, the recommended dose is 5 milligrams twice daily — the standard dose. Dose reduction to 2.5 milligrams twice daily only applies if the patient meets at least two of the three criteria, which yours does not. The ARISTOTLE trial sub-analysis published in 2018 supports the 5-milligram dose in CrCl 15 to 29, with comparable efficacy and bleeding rates to patients with normal renal function. Reference: Eliquis PI section 2.1 and the ARISTOTLE renal sub-study.

### 4. Off-Label Pediatric Use — Prograf (tacrolimus)

**Subject:** `Off-label pediatric use of Prograf — 9-year-old, 28 kg post-transplant`

**Question:**

> The transplant team is initiating Prograf (tacrolimus) for a 9-year-old, 28 kg patient post-liver transplant. The granule formulation is approved for peds but our hospital only stocks the IR capsules. Does the manufacturer have pediatric pharmacokinetic data, case series, or expanded-access program experience that supports using opened-capsule/sprinkle dosing of the IR formulation in this age/weight range?

**Sample Answer (read aloud during the test call):**

> Opening Prograf IR capsules is off-label and we cannot recommend it, but we do have a 2019 case series of 23 pediatric liver transplant patients where the capsule contents were mixed with apple sauce. Trough levels were comparable to the granule formulation when adjusted for weight. We strongly recommend ordering the granules formulation if possible, but if your hospital must use opened capsules, monitor whole-blood trough levels twice weekly for the first month. Reference: Astellas Medical Information case series M-I-C-2019-T-zero-four-two.

### 5. Drug Interaction — Pacerone (amiodarone) + Coumadin (warfarin)

**Subject:** `Interaction — initiating Pacerone in patient on stable Coumadin`

**Question:**

> Patient is 78 years old, on Coumadin (warfarin) 5 mg daily with stable INR 2.4 over the last 6 months. Pacerone (amiodarone) 200 mg daily is being added for new-onset atrial fibrillation. PI notes warfarin interaction. What's the typical magnitude and onset of INR elevation we should expect, and does the manufacturer recommend a pre-emptive warfarin dose reduction (and by what %) or simply increased INR monitoring frequency?

**Sample Answer (read aloud during the test call):**

> Amiodarone potentiates warfarin by inhibiting CYP2C9 and CYP3A4. The expected INR rise is in the range of 30 to 50 percent, with the peak effect appearing between one and three weeks after starting amiodarone. We recommend a pre-emptive reduction of the warfarin dose by 30 to 50 percent at amiodarone initiation, and monitoring INR weekly for the first 4 to 6 weeks, then every 2 weeks until stable. Reference: Pacerone prescribing information, section 7.5.

### 6. Reconstitution / Compounding — Zosyn (piperacillin-tazobactam)

**Subject:** `Zosyn beyond-use date in elastomeric pump for ambulatory OPAT`

**Question:**

> Pharmacy wants to compound Zosyn (piperacillin-tazobactam) 3.375 g in an elastomeric infusion pump for 24-hour ambulatory OPAT (outpatient parenteral antimicrobial therapy) at body-adjacent temperature (~32 °C). The official PI gives stability data for refrigerated storage but limited in-use data for elastomeric devices at warmer temperatures. Does the manufacturer have stability data supporting 24-hour BUD under these conditions?

**Sample Answer (read aloud during the test call):**

> Yes, we have stability data for that exact use case. Zosyn 3.375 grams in 100 milliliters of normal saline is stable for up to 24 hours in elastomeric pumps at body-adjacent temperatures up to 32 degrees Celsius — both potency and sterility are maintained. For runs longer than 24 hours, we'd recommend refrigerated storage between doses or switching to a continuous-infusion protocol. Reference: Pfizer stability study Z-O-S-2022-zero-one-four.

### 7. Therapeutic Substitution — Synthroid → generic levothyroxine

**Subject:** `Switching from Synthroid to generic levothyroxine — NTI concern`

**Question:**

> Patient with Hashimoto's hypothyroidism has been stable on brand Synthroid 125 mcg daily for 8 months (TSH 1.8 mIU/L). The formulary is switching to AB-rated generic levothyroxine. Levothyroxine is widely flagged as a narrow-therapeutic-index drug. Does the manufacturer have any documented case series of clinically meaningful TSH fluctuation in patients switched from Synthroid to AB-rated generics, beyond the standard 80–125% bioequivalence range?

**Sample Answer (read aloud during the test call):**

> While AB-rated generics meet bioequivalence standards, we do have documented cases of TSH fluctuation outside the target range after brand-to-generic switches. In a 2021 case series we sponsored, 156 stable hypothyroid patients were followed for 6 months post-switch — approximately 12 percent had TSH shifts outside their target range within 3 months. We recommend rechecking TSH 6 to 8 weeks after the switch and adjusting the levothyroxine dose if needed. For Hashimoto's patients specifically, the rate was slightly higher at about 15 percent. Reference: AbbVie Synthroid Medical Information case series S-Y-N-2021-C-S-zero-zero-eight.

### 8. Adverse Event Follow-up — Lamictal + Depakote rash

**Subject:** `Reported adverse event — patient on Lamictal and Depakote`

**Question:**

> Patient initiated Lamictal (lamotrigine) 14 days ago for new-onset partial seizures, also on stable Depakote (valproate). Developed a maculopapular rash on day 11 — trunk only, no mucosal involvement, no fever, no eosinophilia. PI lists rash as common and warns valproate ↑ lamotrigine levels by ~2x. Should we hold Lamictal and reassess, or is this consistent with benign rash that resolves? Any manufacturer guidance on re-titration after non-severe rash in patients on concomitant valproate?

**Sample Answer (read aloud during the test call):**

> Valproate inhibits glucuronidation and roughly doubles lamotrigine concentrations, which significantly elevates the risk of serious rash including Stevens-Johnson syndrome. Even a benign-appearing rash on concomitant valproate warrants holding Lamictal immediately. Once the rash resolves, you can attempt re-titration but at half the standard speed — starting at 12.5 milligrams every other day, increasing every two weeks, with a maximum maintenance dose of 100 milligrams per day on concomitant valproate. If the rash recurs at any point, discontinue permanently. Reference: Lamictal prescribing information section 5.1 and GSK medical information letter M-I-L-2022-L-zero-one-nine.

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

