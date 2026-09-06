"""
Basic API tests. These run WITHOUT an API key or network access: the one
endpoint that would call the LLM (POST /digest) is monkeypatched with a fake
digest, so the tests are fast and safe to run in CI.
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

# Make sure we can import main.py from the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main  # noqa: E402

client = TestClient(main.app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_root_lists_endpoints():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "endpoints" in resp.json()


def test_digest_endpoint_with_fake_agent(monkeypatch, tmp_path):
    fake = {
        "markdown": "# Bitcoin News Digest - test\n\nAlles gut.",
        "model": "fake-model",
        "generated_at": "2026-07-17T00:00:00+00:00",
        "focus": "test",
    }
    # Replace the real agent call with a fake so no network/LLM is used.
    monkeypatch.setattr(main, "generate_digest", lambda topic=None: fake)
    # Write the "latest" file into a temp dir so we don't touch real data/.
    monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "LATEST_PATH", str(tmp_path / "latest_digest.json"))

    resp = client.post("/digest", json={"topic": "test"})
    assert resp.status_code == 200
    assert resp.json()["markdown"].startswith("# Bitcoin News Digest")

    # After generating, /digest/latest should return the same digest.
    latest = client.get("/digest/latest")
    assert latest.status_code == 200
    assert latest.json()["model"] == "fake-model"
