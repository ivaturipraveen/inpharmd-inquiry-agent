import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import scheduler
from database import Base, engine
from routers import agent_tools, email_inbound, inquiries, manufacturers, webhooks

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
)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
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
