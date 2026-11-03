"""CORS: closed by default, explicit origins only, never "*" with credentials (fix #11)."""
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

import main

EVIL = "https://evil.example"
GOOD = "https://app.example.com"


def preflight(client, origin, method="POST"):
    return client.options("/legal-assistant", headers={
        "Origin": origin,
        "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": "content-type",
    })


def app_with(origins):
    app = FastAPI()
    app.add_middleware(CORSMiddleware, **main.cors_settings(origins))

    @app.post("/legal-assistant")
    def endpoint():
        return {"ok": True}

    return TestClient(app)


# ---------- default: no cross-origin access ----------

def test_cors_is_off_by_default():
    assert main.ALLOWED_ORIGINS == []
    assert not any(m.cls is CORSMiddleware for m in main.app.user_middleware)


def test_other_sites_get_no_cors_headers_by_default(client, llm_reply):
    llm_reply("answer")
    r = client.post("/legal-assistant", json={"question": "q"}, headers={"Origin": EVIL})
    assert "access-control-allow-origin" not in r.headers
    assert "access-control-allow-credentials" not in r.headers
    assert "access-control-allow-origin" not in preflight(client, EVIL).headers


def test_same_origin_pages_still_work(client, llm_reply):
    llm_reply("answer")
    assert client.post("/legal-assistant", json={"question": "q"}).json() == {"response": "answer"}


# ---------- explicit list ----------

def test_listed_origin_is_allowed_with_credentials():
    r = preflight(app_with([GOOD]), GOOD)
    assert r.headers["access-control-allow-origin"] == GOOD
    assert r.headers["access-control-allow-credentials"] == "true"


def test_unlisted_origin_is_refused():
    r = preflight(app_with([GOOD]), EVIL)
    assert r.headers.get("access-control-allow-origin") != EVIL


@pytest.mark.parametrize("method", ["DELETE", "PUT", "PATCH"])
def test_only_get_and_post_are_allowed(method):
    assert preflight(app_with([GOOD]), GOOD, method).status_code == 400


# ---------- wildcard ----------

def test_wildcard_never_allows_credentials():
    settings = main.cors_settings(["*"])
    assert settings["allow_credentials"] is False
    r = app_with(["*"]).post("/legal-assistant", headers={"Origin": EVIL, "Cookie": "session=abc"})
    assert r.headers.get("access-control-allow-credentials") != "true"
    assert r.headers["access-control-allow-origin"] == "*"  # not echoed back as the caller's origin


# ---------- parsing ----------

@pytest.mark.parametrize("raw, expected", [
    ("", []),
    ("  ", []),
    ("https://a.com", ["https://a.com"]),
    ("https://a.com/, http://localhost:3000 ,", ["https://a.com", "http://localhost:3000"]),
])
def test_allowed_origins_parsing(raw, expected):
    assert main.parse_origins(raw) == expected
