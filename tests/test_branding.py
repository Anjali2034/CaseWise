"""The product is called CaseWise, everywhere, and nothing else."""
import re
from pathlib import Path

import pytest

import main
import prompts

ROOT = Path(__file__).resolve().parent.parent
BRAND = "CaseWise"

# Old names, built from pieces so this file doesn't match itself.
OLD_NAMES = re.compile("|".join([r"legal\s*" + "edge", r"auto\s*" + r"docu\s*gen", "docu" + "gen"]), re.I)

SKIP_DIRS = {".git", "venv", ".venv", "env", "__pycache__", ".pytest_cache", "node_modules"}
SKIP_FILES = {"CHECKLIST.md"}  # private progress notes that record the project's history
TEXT_SUFFIXES = {".py", ".html", ".css", ".js", ".md", ".txt", ".yaml", ".yml", ".ini", ".toml",
                 ".json", ".ipynb", ".example", ""}


def project_files():
    for path in ROOT.rglob("*"):
        if path.is_file() and not SKIP_DIRS & set(path.relative_to(ROOT).parts) \
                and path.name not in SKIP_FILES and path.suffix in TEXT_SUFFIXES:
            yield path


def test_no_old_brand_names_anywhere_in_the_project():
    hits = []
    for path in project_files():
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if OLD_NAMES.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not hits, "old brand names found:\n" + "\n".join(hits)


def test_brand_is_always_spelled_casewise_in_visible_text():
    """Catch near-misses like 'Casewise', 'Case Wise' or 'CaseWise AI' in anything a person reads."""
    bad = re.compile(r"\bcase\s?wise\b(?<!CaseWise)|CaseWise\s+AI\b", re.I)
    for path in [*ROOT.joinpath("templates").rglob("*.html"), ROOT / "main.py", ROOT / "prompts.py"]:
        text = path.read_text(encoding="utf-8")
        wrong = [m.group(0) for m in bad.finditer(text) if m.group(0) != BRAND]
        assert not wrong, f"{path.name}: {wrong}"


PAGES = {"/": f"{BRAND} - Legal Document Analysis Made Simple",
         "/legal-assistant": f"Legal Assistant - {BRAND}",
         "/document-generator": f"Document Generator - {BRAND}",
         "/contract-analyzer": f"Contract Analyzer - {BRAND}",
         "/compliance-checker": f"Compliance Checker - {BRAND}",
         "/document-simplifier": f"Document Simplifier - {BRAND}"}


@pytest.mark.parametrize("path, title", PAGES.items())
def test_every_page_title_and_logo_say_casewise(client, path, title):
    html = client.get(path).text
    assert f"<title>{title}</title>" in html
    assert f'<span class="logo-text">{BRAND}</span>' in html


def test_api_title_is_casewise(client):
    assert main.app.title == BRAND
    assert client.get("/openapi.json").json()["info"]["title"] == BRAND


@pytest.mark.parametrize("name", ["LEGAL_ASSISTANT", "DOCUMENT_GENERATOR", "CONTRACT_ANALYZER",
                                  "COMPLIANCE_CHECKER", "DOCUMENT_SIMPLIFIER"])
def test_the_ai_introduces_itself_as_casewise(name):
    assert f"say you are {BRAND}'s AI assistant" in getattr(prompts, name)


def test_render_service_is_named_casewise():
    assert re.search(r"^\s+name: casewise$", (ROOT / "render.yaml").read_text(encoding="utf-8"), re.M)
