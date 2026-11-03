"""Every API endpoint: success responses and correct HTTP error codes."""
import re

import pytest

import llm_client as oc
from conftest import make_blank_pdf, make_text_pdf

TEXT_ENDPOINTS = [
    ("/legal-assistant", "question"),
    ("/generate-document", "prompt"),
    ("/analyze-contract", "contract_text"),
    ("/check-compliance", "legal_text"),
    ("/simplify-document", "legal_text"),
]
PDF_ENDPOINTS = ["/simplify-document-pdf", "/analyze-contract-pdf", "/check-compliance-pdf"]
ALL_ENDPOINTS = [(ep, key) for ep, key in TEXT_ENDPOINTS] + [(ep, None) for ep in PDF_ENDPOINTS]

ANALYSIS_JSON = '{"key_clauses":[{"name":"Term","purpose":"1 year"}],"potential_risks":["x"],"summary":"ok"}'
COMPLIANCE_JSON = '{"missing_clauses":["Termination"],"compliance_issues":[],"recommendations":[],"summary":"ok"}'


def post(client, endpoint, key, text="This Agreement is made between A and B"):
    if key is None:
        return client.post(endpoint, files={"file": ("doc.pdf", make_text_pdf(text), "application/pdf")})
    return client.post(endpoint, json={key: text})


# ---------- success ----------

@pytest.mark.parametrize("endpoint, key", [TEXT_ENDPOINTS[0], TEXT_ENDPOINTS[1], TEXT_ENDPOINTS[4]])
def test_text_endpoints_return_model_reply(client, llm_reply, endpoint, key):
    llm_reply("Hello ", "world")
    r = post(client, endpoint, key)
    assert r.status_code == 200
    assert r.json() == {"response": "Hello world"}


def test_analyze_contract_returns_parsed_json(client, llm_reply):
    llm_reply(ANALYSIS_JSON)
    r = post(client, "/analyze-contract", "contract_text")
    assert r.status_code == 200
    assert r.json()["key_clauses"][0]["name"] == "Term"


def test_analyze_contract_extracts_json_wrapped_in_prose(client, llm_reply):
    llm_reply("Here you go: ", ANALYSIS_JSON, " Hope it helps!")
    assert post(client, "/analyze-contract", "contract_text").json()["summary"] == "ok"


def test_check_compliance_returns_parsed_json(client, llm_reply):
    llm_reply(COMPLIANCE_JSON)
    assert post(client, "/check-compliance", "legal_text").json()["missing_clauses"] == ["Termination"]


def test_check_compliance_text_fallback(client, llm_reply):
    llm_reply("Missing Clauses:\n\n* Termination clause\n\nSummary:\n\nMostly fine.")
    body = post(client, "/check-compliance", "legal_text").json()
    assert body["missing_clauses"] == ["Termination clause"]
    assert body["summary"] == "Mostly fine."


@pytest.mark.parametrize("endpoint", PDF_ENDPOINTS)
def test_pdf_endpoints_extract_text_and_call_model(client, llm_reply, endpoint):
    reply = {"/simplify-document-pdf": "Plain English.",
             "/analyze-contract-pdf": ANALYSIS_JSON,
             "/check-compliance-pdf": COMPLIANCE_JSON}[endpoint]
    llm_reply(reply)
    assert post(client, endpoint, None).status_code == 200


# ---------- bad input: 400 (fix #4) ----------

def test_empty_text_is_400(client):
    r = client.post("/simplify-document", json={"legal_text": "   "})
    assert r.status_code == 400
    assert r.json()["detail"] == "Legal text cannot be empty"


@pytest.mark.parametrize("endpoint", PDF_ENDPOINTS)
def test_blank_pdf_is_400(client, endpoint):
    r = client.post(endpoint, files={"file": ("a.pdf", make_blank_pdf(), "application/pdf")})
    assert r.status_code == 400
    assert "Could not extract text" in r.json()["detail"]


@pytest.mark.parametrize("endpoint", PDF_ENDPOINTS)
def test_fake_pdf_is_400(client, endpoint):
    r = client.post(endpoint, files={"file": ("a.pdf", b"not a pdf", "application/pdf")})
    assert r.status_code == 400
    assert r.json()["detail"] == "The uploaded file is not a valid PDF"


# ---------- LLM failures map to the right status (fixes #3, #5, #6, #9) ----------

LLM_ERRORS = [
    (oc.OllamaUnavailableError("x"), 503, "Ollama server is not running"),
    (oc.ModelNotFoundError("x"), 503, "ollama pull llama2"),
    (oc.LLMTimeoutError("x"), 504, "took too long"),
    (oc.LLMError("x"), 502, "AI service returned an error"),
]


@pytest.mark.parametrize("endpoint, key", ALL_ENDPOINTS)
@pytest.mark.parametrize("error, status, message", LLM_ERRORS)
def test_llm_errors_map_to_status_on_every_endpoint(client, llm_reply, endpoint, key, error, status, message):
    llm_reply(error=error)
    r = post(client, endpoint, key)
    assert r.status_code == status, r.json()
    assert message in r.json()["detail"]


@pytest.mark.parametrize("endpoint, key", [("/analyze-contract", "contract_text"),
                                           ("/check-compliance", "legal_text"),
                                           ("/analyze-contract-pdf", None),
                                           ("/check-compliance-pdf", None)])
def test_unparseable_model_output_is_502(client, llm_reply, endpoint, key):
    llm_reply("Sorry, I cannot help with that.")
    r = post(client, endpoint, key)
    assert r.status_code == 502
    assert "unexpected format" in r.json()["detail"]


# ---------- no internal details leak (fix #9) ----------

SECRET = r"C:\internal\secret\config.py api_key=xyz"


@pytest.mark.parametrize("error", [RuntimeError(SECRET), KeyError(SECRET), ValueError(SECRET),
                                   oc.LLMError(SECRET)])
def test_internal_error_text_never_reaches_the_user(client, llm_reply, error):
    llm_reply(error=error)
    for endpoint, key in ALL_ENDPOINTS:
        detail = post(client, endpoint, key).json()["detail"]
        assert "secret" not in detail and "api_key" not in detail, (endpoint, detail)


def test_unexpected_error_returns_error_id_that_is_in_the_log(client, llm_reply, caplog):
    llm_reply(error=RuntimeError("boom"))
    r = post(client, "/legal-assistant", "question")
    assert r.status_code == 500
    error_id = re.search(r"error ID: (\w+)", r.json()["detail"]).group(1)
    assert f"Unexpected error [{error_id}]" in caplog.text
    assert "Traceback" in caplog.text
