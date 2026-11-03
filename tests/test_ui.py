"""UI behaviour that the browser depends on (Phase 0 UI fixes)."""
import re
from pathlib import Path

import pytest

STYLES = Path("static/css/styles.css").read_text(encoding="utf-8")

# ---------- loading indicator (fix #14) ----------

LOADING_PAGES = {
    "/contract-analyzer": "Analyzing contract...",
    "/compliance-checker": "Checking compliance...",
    "/document-generator": "Generating document...",
    "/document-simplifier": "Simplifying document...",
}


def test_shared_loading_style_shows_a_spinner_and_the_label():
    assert re.search(r"\.loading::before \{[^}]*animation: loading-spin", STYLES)
    assert re.search(r"\.loading::after \{[^}]*content: attr\(data-label\)", STYLES)
    assert "prefers-reduced-motion" in STYLES


@pytest.mark.parametrize("path, label", LOADING_PAGES.items())
def test_each_tool_page_has_a_labelled_accessible_loading_indicator(client, path, label):
    html = client.get(path).text
    assert f'class="loading" data-label="{label}" role="status" aria-live="polite"' in html


@pytest.mark.parametrize("template", sorted(Path("templates").rglob("*.html")), ids=lambda p: p.name)
def test_no_page_overrides_the_shared_loading_style(template):
    """Per-page copies are how two pages ended up with an invisible spinner."""
    css = template.read_text(encoding="utf-8")
    assert not re.search(r"^\.loading", css, re.M)


def test_only_one_loading_style_exists():
    """An old second .loading rule animated ::after, which made the label spin."""
    assert len(re.findall(r"^\.loading \{", STYLES, re.M)) == 1
    assert len(re.findall(r"^\.loading::after \{", STYLES, re.M)) == 1
    assert "@keyframes spin " not in STYLES


def test_legal_assistant_thinking_bubble_is_labelled_and_visible():
    js = Path("templates/legal-assistant.html").read_text(encoding="utf-8")
    assert "loadingDiv.dataset.label = 'Thinking...'" in js
    assert "loadingDiv.style.display = 'block'" in js  # .loading is hidden by default
    assert "loadingDiv.setAttribute('role', 'status')" in js
    after_bubble = js.split("addLoadingMessage(loadingId);", 1)[1]
    assert after_bubble.lstrip().startswith("chatMessages.scrollTop = chatMessages.scrollHeight")
