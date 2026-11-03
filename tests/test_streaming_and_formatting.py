"""Group 2: streaming answers (L4), formatted answers (L5), JSON mode (L6), Indian law (L7)."""
import json
import re
from pathlib import Path
from unittest import mock

import pytest

import llm_client as oc
import main
import prompts
from conftest import make_text_pdf

STREAM_ENDPOINTS = [("/legal-assistant/stream", "question"), ("/generate-document/stream", "prompt"),
                    ("/simplify-document/stream", "legal_text")]


def events(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def streaming(*pieces, fail_after=None):
    """A fake model stream: yields pieces, optionally raising part-way."""
    def gen(*args, **kwargs):
        for i, piece in enumerate(pieces):
            if fail_after is not None and i == fail_after:
                raise oc.LLMError("model crashed")
            yield piece
    return gen


# ---------- L4: streaming endpoints ----------

@pytest.mark.parametrize("endpoint, key", STREAM_ENDPOINTS)
def test_stream_sends_each_piece_then_done(client, endpoint, key):
    with mock.patch.object(main.llm, "_generate_stream", side_effect=streaming("Hello ", "**world**")):
        r = client.post(endpoint, json={key: "text"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    assert r.headers["cache-control"] == "no-cache"
    assert events(r) == [{"text": "Hello "}, {"text": "**world**"}, {"done": True}]


def test_pdf_stream(client):
    with mock.patch.object(main.llm, "_generate_stream", side_effect=streaming("Plain ", "English")):
        r = client.post("/simplify-document-pdf/stream",
                        files={"file": ("a.pdf", make_text_pdf("This Agreement is made"), "application/pdf")})
    assert events(r) == [{"text": "Plain "}, {"text": "English"}, {"done": True}]


@pytest.mark.parametrize("error, status", [
    (oc.ServiceUnavailableError("x"), 503), (oc.LLMRateLimitedError("x", retry_after=9), 429),
    (oc.DocumentTooLongError("x"), 413), (oc.LLMTimeoutError("x"), 504), (RuntimeError("secret path"), 500),
])
@pytest.mark.parametrize("endpoint, key", STREAM_ENDPOINTS)
def test_errors_before_the_first_words_get_a_real_status(client, endpoint, key, error, status):
    with mock.patch.object(main.llm, "_generate_stream", side_effect=error):
        r = client.post(endpoint, json={key: "text"})
    assert r.status_code == status
    assert "secret" not in r.json()["detail"]


def test_error_part_way_is_reported_in_the_stream(client):
    with mock.patch.object(main.llm, "_generate_stream", side_effect=streaming("Hello ", "never", fail_after=1)):
        r = client.post("/legal-assistant/stream", json={"question": "q"})
    assert r.status_code == 200
    assert events(r) == [{"text": "Hello "}, {"error": "The AI service returned an error. Please try again."}]


def test_empty_stream_is_502(client):
    with mock.patch.object(main.llm, "_generate_stream", side_effect=streaming()):
        r = client.post("/generate-document/stream", json={"prompt": "p"})
    assert r.status_code == 502


@pytest.mark.parametrize("endpoint, key", STREAM_ENDPOINTS)
def test_stream_endpoints_validate_and_rate_limit_like_the_others(client, endpoint, key, monkeypatch):
    assert client.post(endpoint, json={key: "  "}).status_code == 400
    assert client.post(endpoint, json={key: "a" * (main.MAX_TEXT_CHARS + 1)}).status_code == 413
    monkeypatch.setattr(main, "rate_limiter", main.RateLimiter(per_minute=1, per_day=10))
    with mock.patch.object(main.llm, "_generate_stream", side_effect=streaming("ok")):
        assert client.post(endpoint, json={key: "x"}).status_code == 200
        assert client.post(endpoint, json={key: "x"}).status_code == 429


def test_stream_uses_each_tools_own_prompt():
    for method, prompt in [("legal_assistant_stream", prompts.LEGAL_ASSISTANT),
                           ("generate_document_stream", prompts.DOCUMENT_GENERATOR),
                           ("simplify_document_stream", prompts.DOCUMENT_SIMPLIFIER)]:
        with mock.patch.object(main.llm, "_generate_stream", return_value=iter(["x"])) as stream:
            list(getattr(main.llm, method)("text"))
        assert stream.call_args.args[1] == prompt


# ---------- L6: JSON mode for the analysis tools ----------

@pytest.mark.parametrize("client_obj, key, expected", [
    (oc.OllamaClient(), "format", "json"),
    (oc.GroqClient(api_key="k"), "response_format", {"type": "json_object"}),
])
def test_json_mode_reaches_each_provider(client_obj, key, expected):
    _, body, _ = client_obj._request("p", "s", json_mode=True)
    assert body[key] == expected
    _, plain, _ = client_obj._request("p", "s")
    assert key not in plain


@pytest.mark.parametrize("method", ["analyze_contract", "check_compliance"])
def test_analysis_tools_ask_for_json_mode(method):
    reply = '{"key_clauses":[],"potential_risks":[],"missing_clauses":["x"],"compliance_issues":[],"recommendations":[],"summary":"s"}'
    with mock.patch.object(main.llm, "_generate_stream", return_value=iter([reply])) as stream:
        getattr(main.llm, method)("contract")
    assert stream.call_args.kwargs.get("json_mode") is True


@pytest.mark.parametrize("method", ["legal_assistant", "generate_document", "simplify_document"])
def test_text_tools_do_not_use_json_mode(method):
    with mock.patch.object(main.llm, "_generate_stream", return_value=iter(["x"])) as stream:
        getattr(main.llm, method)("text")
    assert not stream.call_args.kwargs.get("json_mode")


# ---------- L7: Indian law by default ----------

ALL_PROMPTS = [prompts.LEGAL_ASSISTANT, prompts.DOCUMENT_GENERATOR, prompts.CONTRACT_ANALYZER,
               prompts.COMPLIANCE_CHECKER, prompts.DOCUMENT_SIMPLIFIER]


@pytest.mark.parametrize("prompt", ALL_PROMPTS)
def test_every_prompt_defaults_to_indian_law(prompt):
    assert "Unless the user says otherwise, give the position under Indian law" in prompt
    assert "Name a specific Act or section only when you are sure it applies" in prompt


def test_generator_drafts_for_india():
    assert "Indian Rupees" in prompts.DOCUMENT_GENERATOR and "DD/MM/YYYY" in prompts.DOCUMENT_GENERATOR
    assert "stamp duty" in prompts.DOCUMENT_GENERATOR


def test_compliance_checks_against_indian_law():
    assert "Review it against Indian law" in prompts.COMPLIANCE_CHECKER
    assert "Indian Contract Act, 1872" in prompts.COMPLIANCE_CHECKER


# ---------- L5: formatted (Markdown) answers, safely ----------

VENDOR = Path("static/vendor")
BASE = Path("templates/base.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("filename, header", [("marked-18.1.1.umd.js", "marked v18.1.1"),
                                              ("purify-3.4.17.min.js", "DOMPurify 3.4.17")])
def test_vendored_libraries_are_the_pinned_versions_with_licences(filename, header):
    assert header in (VENDOR / filename).read_text(encoding="utf-8")[:400]
    assert (VENDOR / "marked-LICENSE.md").exists() and (VENDOR / "dompurify-LICENSE.txt").exists()


def test_libraries_load_before_the_helpers_that_use_them(client):
    html = client.get("/legal-assistant").text
    assert html.index("marked-18.1.1.umd.js") < html.index("purify-3.4.17.min.js") < html.index("function renderMarkdown")
    for path in re.findall(r'src="(/static/vendor/[^"]+)"', html):
        assert client.get(path).status_code == 200


def test_render_markdown_always_sanitizes():
    body = BASE.split("function renderMarkdown(text) {", 1)[1].split("\n        }", 1)[0]
    assert "DOMPurify.sanitize(marked.parse(" in body and "MARKDOWN_SANITIZE" in body


def test_answers_cannot_restyle_the_page_or_show_fake_forms():
    config = BASE.split("const MARKDOWN_SANITIZE = {", 1)[1].split("};", 1)[0]
    assert "FORBID_ATTR: ['style']" in config
    for tag in ["form", "input", "button", "textarea", "select", "style"]:
        assert f"'{tag}'" in config


@pytest.mark.parametrize("template", sorted(Path("templates").rglob("*.html")), ids=lambda p: p.name)
def test_ai_text_only_reaches_innerhtml_through_render_markdown(template):
    """The only innerHTML assignments allowed: sanitized Markdown, or static/escaped template strings."""
    for line in template.read_text(encoding="utf-8").splitlines():
        if ".innerHTML =" in line and "renderMarkdown(" not in line:
            assert "`" in line or "formatAnalysisResponse(" in line or "formatComplianceResponse(" in line, line.strip()
