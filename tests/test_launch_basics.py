"""L3 + L13: Privacy and Terms pages, link previews, error pages, security headers."""
import html as html_lib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from unittest import mock

import main
from security import content_security_policy, security_headers

TOOLS = ["/legal-assistant", "/document-generator", "/contract-analyzer", "/compliance-checker", "/document-simplifier"]
PAGES = ["/", *TOOLS, "/privacy", "/terms"]
HTML = {"accept": "text/html,application/xhtml+xml"}


# ---------- L3: legal pages ----------

@pytest.mark.parametrize("path, title", [("/privacy", "Privacy Policy - CaseWise"), ("/terms", "Terms of Use - CaseWise")])
def test_legal_pages_render(client, path, title):
    r = client.get(path)
    assert r.status_code == 200 and f"<title>{title}</title>" in r.text


@pytest.mark.parametrize("statement", [
    "Groq", "Zero Data Retention", "may be processed outside India", "up to 24 hours", "Render",
    "local storage", "We don't use cookies", "Digital Personal Data Protection Act, 2023", "aged 18 or over",
    "Last updated:",
])
def test_privacy_policy_covers_what_actually_happens(client, statement):
    assert statement in client.get("/privacy").text


@pytest.mark.parametrize("statement", ["not legal advice", "lawyer-client relationship", "AI can be wrong",
                                       "laws of India", "18 or over", "Privacy Policy"])
def test_terms_cover_the_essentials(client, statement):
    assert statement in client.get("/terms").text


@pytest.mark.parametrize("page", PAGES)
def test_the_privacy_claim_of_no_cookies_is_true(client, page):
    assert "set-cookie" not in client.get(page).headers


def test_the_privacy_claim_of_no_tracking_is_true():
    for template in Path("templates").rglob("*.html"):
        text = template.read_text(encoding="utf-8")
        assert "document.cookie" not in text
        assert not re.search(r"googletagmanager|google-analytics|fonts\.googleapis", text), template.name


@pytest.mark.parametrize("page", TOOLS)
def test_every_tool_says_where_the_text_goes_and_links_the_policies(client, page):
    html = client.get(page).text
    assert "Your text is sent to our AI provider" in html
    assert 'href="/privacy"' in html and 'href="/terms"' in html


@pytest.mark.parametrize("page", ["/", "/privacy", "/terms"])
def test_content_pages_have_a_footer_with_policy_links(client, page):
    footer = client.get(page).text.split('<footer class="site-footer">', 1)[1]
    assert 'href="/privacy"' in footer and 'href="/terms"' in footer


@pytest.mark.parametrize("path", ["/privacy", "/terms"])
def test_contact_email_is_published_when_set(client, path):
    with mock.patch.dict(main.templates.env.globals, {"contact_email": "hello@example.com"}):
        assert 'href="mailto:hello@example.com"' in client.get(path).text
    with mock.patch.dict(main.templates.env.globals, {"contact_email": ""}):
        assert "contact details will be added here soon" in client.get(path).text


# ---------- L13: link previews ----------

@pytest.mark.parametrize("page", PAGES)
def test_every_page_has_a_description_and_link_preview(client, page):
    html = client.get(page).text
    description = re.search(r'<meta name="description" content="([^"]+)"', html).group(1)
    assert 30 < len(description) <= 170, description
    assert f'<meta property="og:description" content="{description}"' in html
    title = re.search(r"<title>(.*?)</title>", html).group(1)
    assert f'<meta property="og:title" content="{title}"' in html
    assert 'name="twitter:card" content="summary_large_image"' in html


def test_preview_links_are_full_addresses_on_the_public_site(client):
    with mock.patch.dict(main.templates.env.globals, {"site_url": "https://casewise.example"}):
        html = client.get("/contract-analyzer").text
    assert '<meta property="og:url" content="https://casewise.example/contract-analyzer">' in html
    assert '<meta property="og:image" content="https://casewise.example/static/og-image.png">' in html


def test_preview_image_is_the_size_platforms_expect(client):
    data = client.get("/static/og-image.png").content
    assert data[:4] == b"\x89PNG"
    assert int.from_bytes(data[16:20], "big") == 1200 and int.from_bytes(data[20:24], "big") == 630


@pytest.mark.parametrize("page", PAGES)
def test_assets_use_paths_that_work_behind_a_proxy(client, page):
    """Absolute http:// URLs from url_for would be blocked as mixed content on the HTTPS site."""
    html = client.get(page).text
    assert not re.search(r'(?:href|src)="http://', html)
    assert 'href="/static/css/styles.css"' in html


# ---------- L13: error pages ----------

def test_wrong_address_shows_a_designed_page_to_people(client):
    r = client.get("/no-such-page", headers=HTML)
    assert r.status_code == 404
    assert "We couldn't find that page" in html_lib.unescape(r.text) and 'class="logo-text"' in r.text
    assert 'name="robots" content="noindex"' in r.text


def test_opening_an_api_address_in_a_browser_shows_the_page_too(client):
    r = client.get("/generate-document", headers=HTML)
    assert r.status_code == 404 and "We couldn't find that page" in html_lib.unescape(r.text)


def test_api_clients_still_get_json_errors(client):
    r = client.get("/no-such-page")
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    r = client.post("/simplify-document", json={"legal_text": "x" * (main.MAX_TEXT_CHARS + 1)}, headers=HTML)
    assert r.status_code == 413 and "too long" in r.json()["detail"]


@pytest.mark.parametrize("headers, is_html", [(HTML, True), ({}, False)])
def test_unexpected_crash_reveals_nothing_and_gives_an_id(headers, is_html, caplog):
    app_client = TestClient(main.app, raise_server_exceptions=False)
    with mock.patch.object(main.templates.env, "globals", {**main.templates.env.globals}), \
            mock.patch.object(main.llm, "check_health", side_effect=RuntimeError("secret path C:\\x")):
        r = app_client.get("/health", headers=headers)
    assert r.status_code == 500 and "secret" not in r.text
    error_id = re.search(r"error ID:? (\w{8})", r.text).group(1)
    assert f"Unhandled error [{error_id}]" in caplog.text
    assert ("Something went wrong on our side" in r.text) is is_html
    assert "Content-Security-Policy" in r.headers


# ---------- L13: security headers ----------

@pytest.mark.parametrize("path", [*PAGES, "/health", "/static/css/styles.css"])
def test_security_headers_on_every_response(client, path):
    headers = client.get(path).headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in headers["permissions-policy"]
    assert "content-security-policy" in headers


def test_csp_is_strict():
    csp = content_security_policy("abc")
    assert "script-src 'self' 'nonce-abc'" in csp and "style-src 'self' 'nonce-abc'" in csp
    assert "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp
    assert "img-src 'self' data:" in csp      # AI answers can't leak data through outside images
    assert "connect-src 'self'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp


@pytest.mark.parametrize("page", PAGES)
def test_every_inline_block_carries_this_requests_nonce(client, page):
    r = client.get(page)
    nonce = re.search(r"'nonce-([^']+)'", r.headers["content-security-policy"]).group(1)
    for tag, attrs in re.findall(r"<(script|style)\b([^>]*)>", r.text):
        if "src=" not in attrs:
            assert f'nonce="{nonce}"' in attrs, (tag, attrs)


def test_nonce_changes_on_every_request(client):
    nonce = lambda: re.search(r"'nonce-([^']+)'", client.get("/").headers["content-security-policy"]).group(1)  # noqa: E731
    assert nonce() != nonce()


@pytest.mark.parametrize("template", sorted(Path("templates").rglob("*.html")), ids=lambda p: p.name)
def test_no_inline_style_attributes(template):
    """The CSP blocks style="..." attributes; they would silently do nothing."""
    assert not re.search(r"<[a-zA-Z][^>]*\sstyle=", template.read_text(encoding="utf-8"))


def test_api_docs_keep_working(client):
    r = client.get("/docs")
    assert r.status_code == 200 and "content-security-policy" not in r.headers
    assert r.headers["x-frame-options"] == "DENY"


def test_hsts_only_over_https():
    assert "Strict-Transport-Security" not in security_headers("n", "/", https=False)
    assert security_headers("n", "/", https=True)["Strict-Transport-Security"] == "max-age=31536000"


def test_https_is_detected_behind_the_proxy(client):
    assert "strict-transport-security" not in client.get("/").headers
    assert "strict-transport-security" in client.get("/", headers={"x-forwarded-proto": "https"}).headers


def test_streaming_passes_through_the_middleware(client):
    with mock.patch.object(main.llm, "_generate_stream", side_effect=lambda *a, **k: iter(["a", "b"])):
        r = client.post("/legal-assistant/stream", json={"question": "q"})
    assert r.status_code == 200 and '"text": "b"' in r.text and "content-security-policy" in r.headers
