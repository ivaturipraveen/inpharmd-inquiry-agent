import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import scheduler
from database import Base, engine
from routers import (
    agent_tools,
    auth,
    email_inbound,
    external_inquiries,
    inquiries,
    manufacturers,
    voice,
    webhooks,
)

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")

app = FastAPI(title="InpharmD Manufacturer MI Contacts API", version="1.0.0")

origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Expose cache + upstream-debug headers so the browser can show them.
    expose_headers=["X-Cache", "X-Cache-Age", "X-Upstream-Error"],
)


def _ensure_columns():
    """Lightweight in-place migration. SQLAlchemy's create_all only creates
    missing tables, not missing columns. Add new nullable columns here so
    redeploys don't require a real migration tool."""
    from sqlalchemy import text
    statements = [
        "ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS pdf_url TEXT",
        "ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS pdf_filename VARCHAR(512)",
        "ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS pdf_summary TEXT",
        "ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS source_inquiry_uuid VARCHAR(128)",
        "CREATE INDEX IF NOT EXISTS ix_inquiries_source_inquiry_uuid ON inquiries (source_inquiry_uuid)",
        "ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS legacy_response_posted_at TIMESTAMPTZ",
        "ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE SET NULL",
        "CREATE INDEX IF NOT EXISTS ix_inquiries_user_id ON inquiries (user_id)",
        # MUE excel attachment metadata + response writeback tracking
        "ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS source_excel_url TEXT",
        "ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS source_excel_sheet VARCHAR(255)",
        "ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS source_excel_row INTEGER",
        "ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS excel_response_url TEXT",
        "ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS excel_response_posted_at TIMESTAMPTZ",
        # Per-row product details from the MUE Excel
        "ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS medication_name TEXT",
        "ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS pi_storage_data TEXT",
        # MUE / forwarded inquiry titles can exceed 255 chars.
        "ALTER TABLE inquiries ALTER COLUMN subject TYPE VARCHAR(1000)",
        # users table: handled by Base.metadata.create_all, but add columns
        # here when the table grows in future iterations.
        # Inbound attachment rows — one per file attached to a manufacturer reply.
        """
CREATE TABLE IF NOT EXISTS inquiry_attachments (
    id            SERIAL PRIMARY KEY,
    inquiry_id    INTEGER NOT NULL REFERENCES inquiries(id) ON DELETE CASCADE,
    url           TEXT NOT NULL,
    filename      VARCHAR(512),
    content_type  VARCHAR(128),
    summary       TEXT,
    display_order INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""",
        "CREATE INDEX IF NOT EXISTS ix_inquiry_attachments_inquiry_id ON inquiry_attachments (inquiry_id)",
        # Guard for deployments where the table was created before created_at was added.
        "ALTER TABLE inquiry_attachments ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        # Per-reply email thread storage — one row per manufacturer reply.
        """
CREATE TABLE IF NOT EXISTS email_replies (
    id               SERIAL PRIMARY KEY,
    inquiry_id       INTEGER NOT NULL REFERENCES inquiries(id) ON DELETE CASCADE,
    direction        VARCHAR(10) NOT NULL,
    sender_email     VARCHAR(255),
    body             TEXT,
    sent_at          TIMESTAMPTZ NOT NULL,
    graph_message_id VARCHAR(512)
)
""",
        "CREATE INDEX IF NOT EXISTS ix_email_replies_inquiry_id ON email_replies (inquiry_id)",
        # Prevents the same Graph/IMAP message from being inserted twice.
        "CREATE UNIQUE INDEX IF NOT EXISTS uix_email_replies_inquiry_graph_msg ON email_replies (inquiry_id, graph_message_id) WHERE graph_message_id IS NOT NULL",
        # Link each attachment to the specific reply it arrived with.
        "ALTER TABLE inquiry_attachments ADD COLUMN IF NOT EXISTS reply_id INTEGER REFERENCES email_replies(id) ON DELETE SET NULL",
        "CREATE INDEX IF NOT EXISTS ix_inquiry_attachments_reply_id ON inquiry_attachments (reply_id)",
        # DailyMed NDC enrichment — PI link + storage text per inquiry
        "ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS pi_link TEXT",
        # Persistent cache for DailyMed NDC lookups (avoids repeated API calls)
        """
CREATE TABLE IF NOT EXISTS dailymed_cache (
    ndc        VARCHAR(50) PRIMARY KEY,
    setid      VARCHAR(128),
    pi_link    TEXT,
    pi_storage TEXT,
    fetched_at TIMESTAMPTZ NOT NULL
)
""",
        # Cross-path dedup: same physical email identified by SMTP Message-ID header
        # regardless of whether it arrived via Graph, IMAP, or SendGrid webhook.
        "ALTER TABLE email_replies ADD COLUMN IF NOT EXISTS smtp_message_id VARCHAR(512)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uix_email_replies_inquiry_smtp_msg ON email_replies (inquiry_id, smtp_message_id) WHERE smtp_message_id IS NOT NULL",
        # Tracks how many attachment URLs were sent in the most recent legacy POST,
        # so maybe_post_for_inquiry can re-POST when new attachments have arrived.
        "ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS legacy_attachment_url_count INTEGER NOT NULL DEFAULT 0",
    ]
    # Each statement runs in its own transaction so a Postgres error on one
    # statement does not abort the rest (a single engine.begin() block puts
    # all subsequent conn.execute() calls in aborted state after any failure).
    for sql in statements:
        try:
            with engine.begin() as conn:
                conn.execute(text(sql))
        except Exception as e:
            logging.getLogger("startup").warning("Migration step failed (%s): %s", sql, e)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    _ensure_columns()
    scheduler.start_scheduler()


@app.on_event("shutdown")
def on_shutdown():
    scheduler.stop_scheduler()


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(manufacturers.router)
app.include_router(inquiries.router)
app.include_router(webhooks.router)
app.include_router(agent_tools.router)
app.include_router(email_inbound.router)
app.include_router(voice.router)
app.include_router(auth.router)
app.include_router(external_inquiries.router)
