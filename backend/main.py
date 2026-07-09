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
    ]
    with engine.begin() as conn:
        for sql in statements:
            try:
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
