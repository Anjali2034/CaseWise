"""Pages render, the app starts without Ollama, and /health reports status."""
import inspect
from unittest import mock

import pytest

import main

PAGES = ["/", "/legal-assistant", "/document-generator", "/contract-analyzer",
         "/compliance-checker", "/document-simplifier"]


@pytest.mark.parametrize("path", PAGES)
def test_pages_render_even_when_ollama_is_down(client, path):
    assert client.get(path).status_code == 200


def test_health_reports_ollama_down(client):
    body = client.get("/health").json()
    assert body == {"status": "degraded", "ollama_reachable": False,
                    "model": "llama2", "model_available": False}


@pytest.mark.parametrize("installed, expected_status, expected_available", [
    ([], "degraded", False),
    ([{"name": "llama2:latest"}], "ok", True),   # tagged name still matches
    ([{"name": "llama2"}], "ok", True),
    ([{"name": "mistral:latest"}], "degraded", False),
])
def test_health_model_detection(client, installed, expected_status, expected_available):
    fake = mock.Mock(status_code=200)
    fake.json.return_value = {"models": installed}
    with mock.patch("llm_client.requests.get", return_value=fake):
        body = client.get("/health").json()
    assert body["ollama_reachable"] is True
    assert body["status"] == expected_status
    assert body["model_available"] is expected_available


def test_model_is_configurable():
    from llm_client import OllamaClient
    assert OllamaClient(model="llama3").model == "llama3"
    assert main.llm.model == "llama2"  # from OLLAMA_MODEL in conftest


def test_llm_endpoints_are_sync_so_they_run_in_a_threadpool():
    """`async def` + blocking requests would freeze the whole server (fix #2)."""
    for name in ["legal_assistant", "generate_document", "analyze_contract", "check_compliance",
                 "simplify_document", "simplify_document_pdf", "analyze_contract_pdf",
                 "check_compliance_pdf"]:
        assert not inspect.iscoroutinefunction(getattr(main, name)), name
