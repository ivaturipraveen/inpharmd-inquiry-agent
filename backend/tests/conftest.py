"""Test-suite-wide safety net: strip every credential/webhook env var that
could let a test talk to a real external service, before any test module
gets a chance to `import main` / `import database` (which call
`load_dotenv()` and would otherwise pick up whatever is in backend/.env).

pytest always imports a directory's conftest.py before collecting/importing
any test module in that directory, so this runs first regardless of import
order in individual test files.

Without this, a test that exercises an endpoint end-to-end via FastAPI's
TestClient — without explicitly mocking the relevant service module — will
make a REAL outbound call using whatever real credentials happen to be in
the local .env (this is exactly how repeated test runs ended up posting
fixture data like "Mfr0"/"GoodOne" to the team's real Slack channel).

This must run before `main`/`database` are imported anywhere, so it lives
at module level (not in a fixture) and executes at conftest import time.
"""
import os

_UNSET_FOR_TESTS = (
    # Slack — the incident this file exists to prevent.
    "SLACK_WEBHOOK_URL",
    # SendGrid outbound email.
    "SENDGRID_API_KEY",
    # ElevenLabs outbound voice calls + inbound webhook signature.
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_INQUIRY_AGENT_ID",
    "ELEVENLABS_INQUIRY_PHONE_NUMBER_ID",
    "ELEVENLABS_WEBHOOK_SECRET",
    # Agent-tools mid-call submit-answer auth.
    "AGENT_TOOLS_SECRET",
    # OpenAI GPT transcript/PDF summarization.
    "OPENAI_API_KEY",
    # Microsoft Graph mailbox polling.
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    # S3/R2 file storage.
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    # POSTs final answers back to the staging InpharmD platform.
    "LEGACY_RESPONSE_API_KEY",
)

for _key in _UNSET_FOR_TESTS:
    os.environ.pop(_key, None)
