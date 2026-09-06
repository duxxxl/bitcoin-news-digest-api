"""
FastAPI app that serves the Bitcoin news digest.

Endpoints:
- GET  /            -> basic info about the service
- GET  /health      -> health check (used later by Docker/hosting/CI)
- POST /digest      -> generate a fresh digest (optionally with a focus topic)
- GET  /digest/latest -> return the most recently generated digest

Run locally with:
    uvicorn main:app --reload
Then open http://127.0.0.1:8000/docs for an interactive UI.
"""

import json
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from orchestrator import generate_digest

DATA_DIR = "data"
LATEST_PATH = os.path.join(DATA_DIR, "latest_digest.json")

app = FastAPI(
    title="Bitcoin News Digest API",
    description="Generates a short daily Bitcoin news digest using an LLM agent.",
    version="0.1.0",
)


class DigestRequest(BaseModel):
    topic: str | None = None  # optional focus, e.g. "ETF flows" or "regulation"


def _save_latest(digest: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LATEST_PATH, "w", encoding="utf-8") as f:
        json.dump(digest, f, ensure_ascii=False, indent=2)


def _load_latest() -> dict | None:
    if not os.path.exists(LATEST_PATH):
        return None
    with open(LATEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@app.get("/")
def root() -> dict:
    return {
        "service": "Bitcoin News Digest API",
        "version": "0.1.0",
        "endpoints": ["/health", "/digest (POST)", "/digest/latest", "/docs"],
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/digest")
def create_digest(request: DigestRequest) -> dict:
    try:
        digest = generate_digest(topic=request.topic)
    except Exception as exc:
        # Turn any agent/API failure into a clean 502 instead of a stack trace.
        raise HTTPException(status_code=502, detail=str(exc))

    _save_latest(digest)
    return digest


@app.get("/digest/latest")
def latest_digest() -> dict:
    digest = _load_latest()
    if digest is None:
        raise HTTPException(
            status_code=404,
            detail="No digest generated yet. Call POST /digest first.",
        )
    return digest
