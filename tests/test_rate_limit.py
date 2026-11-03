"""Per-visitor rate limits on the AI endpoints (fix #27)."""
import pytest

import main
from rate_limit import DAY, MINUTE, RateLimiter, client_key


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


# ---------- RateLimiter ----------

def test_per_minute_limit_and_retry_after():
    clock = Clock()
    limiter = RateLimiter(per_minute=3, per_day=100, clock=clock)
    assert [limiter.check("a") for _ in range(3)] == [None, None, None]
    assert limiter.check("a") == (61, "minute")
    clock.now += 30
    assert limiter.check("a") == (31, "minute")
    clock.now += 31
    assert limiter.check("a") is None          # a minute after the first request


def test_per_day_limit():
    clock = Clock()
    limiter = RateLimiter(per_minute=0, per_day=2, clock=clock)
    assert limiter.check("a") is None and limiter.check("a") is None
    retry, window = limiter.check("a")
    assert window == "day" and 0 < retry <= DAY + 1
    clock.now += DAY
    assert limiter.check("a") is None


def test_visitors_are_counted_separately():
    limiter = RateLimiter(per_minute=1, per_day=10, clock=Clock())
    assert limiter.check("a") is None
    assert limiter.check("b") is None
    assert limiter.check("a") is not None


def test_blocked_requests_do_not_count():
    clock = Clock()
    limiter = RateLimiter(per_minute=1, per_day=2, clock=clock)
    limiter.check("a")
    for _ in range(5):
        assert limiter.check("a")[1] == "minute"   # refused, not recorded
    clock.now += MINUTE
    assert limiter.check("a") is None              # still has 1 of its 2 daily requests


def test_zero_disables():
    limiter = RateLimiter(per_minute=0, per_day=0)
    assert not limiter.enabled
    assert all(limiter.check("a") is None for _ in range(1000))


def test_memory_stays_bounded():
    clock = Clock()
    limiter = RateLimiter(per_minute=5, per_day=5, clock=clock)
    for i in range(10_001):
        limiter.check(f"visitor-{i}")
    clock.now += DAY
    limiter.check("new")
    assert len(limiter._hits) < 10


# ---------- which visitor is it? ----------

def test_proxy_headers_are_ignored_unless_trusted():
    assert client_key("10.0.0.1", {"True-Client-IP": "1.2.3.4", "X-Forwarded-For": "5.6.7.8"}, False) == "10.0.0.1"


def test_cloudflare_headers_are_preferred():
    headers = {"X-Forwarded-For": "6.6.6.6, 9.9.9.9", "CF-Connecting-IP": "1.2.3.4"}
    assert client_key("10.0.0.1", headers, True) == "1.2.3.4"
    assert client_key("10.0.0.1", {"true-client-ip": "4.3.2.1", **headers}, True) == "4.3.2.1"


def test_forwarded_for_uses_the_last_hop_not_the_spoofable_first():
    """A client can send its own X-Forwarded-For; the proxy appends the real hop at the end."""
    assert client_key("10.0.0.1", {"X-Forwarded-For": "6.6.6.6, 203.0.113.9"}, True) == "203.0.113.9"


def test_falls_back_to_the_connection_address():
    assert client_key("10.0.0.1", {}, True) == "10.0.0.1"
    assert client_key(None, {}, False) == "unknown"


# ---------- the endpoints ----------

AI_ENDPOINTS = [("/legal-assistant", "question"), ("/generate-document", "prompt"),
                ("/analyze-contract", "contract_text"), ("/check-compliance", "legal_text"),
                ("/simplify-document", "legal_text")]
PDF_ENDPOINTS = ["/simplify-document-pdf", "/analyze-contract-pdf", "/check-compliance-pdf"]


@pytest.fixture
def strict_limits(monkeypatch):
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(per_minute=2, per_day=3))


@pytest.mark.parametrize("endpoint, key", AI_ENDPOINTS)
def test_every_ai_endpoint_is_limited(client, llm_reply, strict_limits, endpoint, key):
    llm_reply('{"key_clauses":[],"potential_risks":[],"missing_clauses":["x"],"compliance_issues":[],"recommendations":[],"summary":"s"}')
    codes = [client.post(endpoint, json={key: "text"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    r = client.post(endpoint, json={key: "text"})
    assert r.json()["detail"].startswith("You're sending requests too quickly")
    assert int(r.headers["Retry-After"]) > 0


@pytest.mark.parametrize("endpoint", PDF_ENDPOINTS)
def test_pdf_endpoints_are_limited(client, strict_limits, endpoint):
    codes = [client.post(endpoint, files={"file": ("a.pdf", b"junk", "application/pdf")}).status_code for _ in range(3)]
    assert codes == [400, 400, 429]


def test_daily_limit_message(client, llm_reply, monkeypatch):
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(per_minute=0, per_day=1))
    llm_reply("ok")
    client.post("/legal-assistant", json={"question": "q"})
    r = client.post("/legal-assistant", json={"question": "q"})
    assert r.status_code == 429 and "today's limit of 1 requests" in r.json()["detail"]


def test_pages_and_health_are_never_limited(client, monkeypatch):
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(per_minute=1, per_day=1))
    for _ in range(5):
        assert client.get("/").status_code == 200
        assert client.get("/contract-analyzer").status_code == 200
        assert client.get("/health").status_code == 200
