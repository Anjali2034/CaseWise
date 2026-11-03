"""Every tool shows a "not legal advice" notice; every prompt carries the safety rules (fix #13)."""
from unittest import mock

import pytest

import main
import prompts

TOOL_PAGES = {
    "/legal-assistant": "may not apply to your situation or jurisdiction",
    "/document-generator": "drafts and must be reviewed",
    "/contract-analyzer": "may miss risks",
    "/compliance-checker": "not a complete compliance review",
    "/document-simplifier": "may leave out details",
}


@pytest.mark.parametrize("path, note", TOOL_PAGES.items())
def test_every_tool_page_shows_the_disclaimer(client, path, note):
    html = client.get(path).text
    assert "<strong>Not legal advice.</strong>" in html
    assert note in html
    assert "qualified lawyer" in html
    assert html.count('class="disclaimer"') == 1


def test_old_chatbot_disclaimer_is_gone(client):
    assert "inaccurate information about people, places, or facts" not in client.get("/legal-assistant").text


# method name -> (prompt it must use, JSON reply the fake model gives back)
TOOLS = {
    "legal_assistant": (prompts.LEGAL_ASSISTANT, "answer"),
    "generate_document": (prompts.DOCUMENT_GENERATOR, "doc"),
    "analyze_contract": (prompts.CONTRACT_ANALYZER, '{"key_clauses":[],"potential_risks":[],"summary":"s"}'),
    "check_compliance": (prompts.COMPLIANCE_CHECKER,
                         '{"missing_clauses":["x"],"compliance_issues":[],"recommendations":[],"summary":"s"}'),
    "simplify_document": (prompts.DOCUMENT_SIMPLIFIER, "simple"),
}


@pytest.mark.parametrize("method", TOOLS)
def test_each_tool_sends_its_own_system_prompt(method):
    expected_prompt, reply = TOOLS[method]
    with mock.patch.object(main.llm, "_generate_stream", return_value=iter([reply])) as stream:
        getattr(main.llm, method)("some legal text")
    assert stream.call_args.args[1] == expected_prompt


@pytest.mark.parametrize("prompt", [p for p, _ in TOOLS.values()])
def test_every_prompt_carries_the_safety_rules(prompt):
    assert prompt.endswith(prompts.SAFETY_RULES) or prompts.SAFETY_RULES + prompts.JSON_ONLY in prompt
    assert "not legal advice" in prompt
    assert "Never invent laws" in prompt


@pytest.mark.parametrize("prompt", [prompts.CONTRACT_ANALYZER, prompts.COMPLIANCE_CHECKER])
def test_json_prompts_still_demand_json_only(prompt):
    assert prompt.endswith(prompts.JSON_ONLY)


def test_generator_uses_placeholders_instead_of_inventing_details():
    assert "[PARTY A NAME]" in prompts.DOCUMENT_GENERATOR
    assert "Never invent these details" in prompts.DOCUMENT_GENERATOR


def test_simplifier_must_not_add_advice():
    assert "Do not add advice" in prompts.DOCUMENT_SIMPLIFIER


@pytest.mark.parametrize("prompt", [p for p, _ in TOOLS.values()])
def test_prompts_have_no_stray_indentation(prompt):
    """Old prompts sent 8 spaces of source-code indentation to the model on every line."""
    assert not any(line.startswith("        ") for line in prompt.splitlines()
                   if not line.strip().startswith(("{", "}", '"')))
