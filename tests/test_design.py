"""Ivory & Ink design (L10): readable contrast, self-hosted fonts, icons, layout."""
import re
from pathlib import Path

import pytest

STYLES = Path("static/css/styles.css").read_text(encoding="utf-8")
ROOT = re.search(r":root \{(.*?)\}", STYLES, re.S).group(1)
TOKENS = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", ROOT))


def luminance(hex_colour):
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a, b):
    la, lb = sorted([luminance(a), luminance(b)], reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("text", ["--text-color", "--text-muted", "--text-faint", "--primary-color", "--brand-ink", "--danger"])
@pytest.mark.parametrize("background", ["--bg-color", "--card-bg", "--surface-2"])
def test_text_colours_meet_wcag_aa(text, background):
    """WCAG AA: at least 4.5:1 for normal-size text."""
    ratio = contrast(TOKENS[text], TOKENS[background])
    assert ratio >= 4.5, f"{text} on {background}: {ratio:.2f}:1"


def test_button_text_is_readable():
    assert contrast(TOKENS["--on-primary"], TOKENS["--primary-color"]) >= 4.5
    assert contrast(TOKENS["--on-primary"], TOKENS["--accent-color"]) >= 4.5   # hover state


def test_no_dark_theme_leftovers():
    """White-on-dark values would be invisible on the ivory background."""
    for path in [Path("static/css/styles.css"), *Path("templates").rglob("*.html")]:
        assert not re.search(r"rgba\(255,\s*255,\s*255", path.read_text(encoding="utf-8")), path.name


# ---------- fonts and icons ----------

def test_fonts_are_self_hosted(client):
    html = client.get("/").text
    assert "fonts.googleapis.com" not in html and "fonts.gstatic.com" not in html
    fonts_css = Path("static/fonts/fonts.css").read_text(encoding="utf-8")
    for family in ["Inter", "Source Serif 4"]:
        assert f"font-family: '{family}'" in fonts_css
    for path in re.findall(r"url\((/static/fonts/[^)]+)\)", fonts_css):
        r = client.get(path)
        assert r.status_code == 200 and r.content[:4] == b"wOF2", path   # real woff2 files
    for licence in ["inter-OFL.txt", "source-serif-4-OFL.txt"]:
        assert "SIL OPEN FONT LICENSE" in (Path("static/fonts") / licence).read_text(encoding="utf-8").upper()


def test_fonts_are_the_ones_the_stylesheet_asks_for():
    assert '--font-body: "Inter"' in ROOT and '--font-display: "Source Serif 4"' in ROOT


@pytest.mark.parametrize("path, magic", [("/static/favicon.svg", b"<svg"), ("/static/favicon-32.png", b"\x89PNG"),
                                         ("/static/apple-touch-icon.png", b"\x89PNG")])
def test_icons_exist(client, path, magic):
    r = client.get(path)
    assert r.status_code == 200 and r.content.startswith(magic)


@pytest.mark.parametrize("page", ["/", "/legal-assistant", "/contract-analyzer"])
def test_every_page_links_the_icons_and_theme(client, page):
    html = client.get(page).text
    for needle in ['rel="icon"', 'rel="apple-touch-icon"', 'name="theme-color"', "fonts/fonts.css", 'class="logo-mark"']:
        assert needle in html, needle


def test_home_page_uses_line_icons_not_emoji(client):
    html = client.get("/").text
    assert not re.search("[\U0001F300-\U0001FAFF\u2705\u2696]", html)
    assert html.count('class="icon"') >= 8


def test_tool_cards_are_links_to_the_tools(client):
    html = client.get("/").text
    for href in ["/legal-assistant", "/document-generator", "/contract-analyzer", "/compliance-checker", "/document-simplifier"]:
        assert f'class="feature-card" href="{href}"' in html


def test_home_privacy_card_describes_the_live_ai_honestly(client):
    html = client.get("/").text
    assert "Runs on Ollama, which can run on your own computer" not in html   # not true of the live site
    assert "doesn't train on your documents" in html


# ---------- navigation and layout ----------

@pytest.mark.parametrize("page", ["/legal-assistant", "/document-generator", "/contract-analyzer",
                                  "/compliance-checker", "/document-simplifier"])
def test_current_page_is_marked_in_the_navbar(client, page):
    html = client.get(page).text
    assert f'href="{page}" class="nav-link" aria-current="page"' in html
    assert html.count('aria-current="page"') == 2          # desktop navbar + phone menu


@pytest.mark.parametrize("page", ["/document-generator", "/contract-analyzer", "/compliance-checker", "/document-simplifier"])
def test_tool_pages_scroll_as_one_page(client, page):
    assert 'class="chat-container tool-page"' in client.get(page).text
    assert re.search(r"\.tool-page \.chat-messages \{[^}]*overflow: visible", STYLES)


def test_phones_can_reach_saved_chats(client):
    html = client.get("/legal-assistant").text
    assert 'id="chatsToggle"' in html and 'aria-controls="chatSidebar"' in html
    la = Path("templates/legal-assistant.html").read_text(encoding="utf-8")
    assert ".chat-sidebar.open" in la and "function setSidebarOpen(open)" in la


def test_reduced_motion_is_respected():
    assert "@keyframes fadeIn" in STYLES
    assert STYLES.count("@media (prefers-reduced-motion: reduce)") >= 1
