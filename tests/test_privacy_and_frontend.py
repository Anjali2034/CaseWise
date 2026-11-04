"""User content never reaches logs; templates are safe and valid."""
import logging
import re
from pathlib import Path

import esprima
import pytest

from conftest import make_text_pdf

TEMPLATES = sorted(Path("templates").rglob("*.html"))
MARKER = "SECRETCLIENTNAME"


def test_user_content_and_filenames_never_logged(client, llm_reply, caplog):
    caplog.set_level(logging.DEBUG)
    reply = ('{"key_clauses":[],"potential_risks":[],"missing_clauses":["%s"],'
             '"compliance_issues":[],"recommendations":[],"summary":"%s"}' % (MARKER, MARKER))
    llm_reply(f"model reply mentioning {MARKER} ", reply)
    for endpoint, key in [("/legal-assistant", "question"), ("/generate-document", "prompt"),
                          ("/analyze-contract", "contract_text"), ("/check-compliance", "legal_text"),
                          ("/simplify-document", "legal_text")]:
        assert client.post(endpoint, json={key: f"Contract with {MARKER}"}).status_code == 200
    pdf = make_text_pdf(f"Agreement with {MARKER}")
    for endpoint in ["/simplify-document-pdf", "/analyze-contract-pdf", "/check-compliance-pdf"]:
        r = client.post(endpoint, files={"file": (f"{MARKER}_divorce.pdf", pdf, "application/pdf")})
        assert r.status_code == 200
    client.post("/analyze-contract-pdf", files={"file": (f"{MARKER}.pdf", b"junk", "application/pdf")})
    assert caplog.records, "expected some log output"
    assert MARKER not in caplog.text


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_template_javascript_parses(template):
    for script in re.findall(r"<script[^>]*>(.*?)</script>", template.read_text(encoding="utf-8"), re.S):
        esprima.parseScript(script)


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_no_console_log_of_user_data(template):
    assert "console.log" not in template.read_text(encoding="utf-8")


# DOM APIs that always treat a string as plain text, never as HTML
SAFE_LINE_MARKERS = ("textContent", "className", "new Error", "addMessage(",
                     "setAttribute(", ".download =", "confirm(")


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_raw_values_are_escaped_before_reaching_html(template):
    """XSS guard: a bare ${value} may only appear where the browser treats it as text.

    Anything else (innerHTML template strings) must wrap the value in escapeHtml(...).
    Expressions with calls or ternaries, like ${items.map(...)} or ${escapeHtml(x)},
    are not bare values and are checked through their inner parts.
    """
    for number, line in enumerate(template.read_text(encoding="utf-8").splitlines(), 1):
        for value in re.findall(r"\$\{\s*([\w.]+)\s*\}", line):
            assert any(m in line for m in SAFE_LINE_MARKERS), (
                f"{template.name}:{number}: unescaped ${{{value}}} -> wrap it in escapeHtml()"
            )
