"""UI behaviour that the browser depends on (Phase 0 UI fixes)."""
import re
from pathlib import Path

import pytest

STYLES = Path("static/css/styles.css").read_text(encoding="utf-8")

# ---------- no dead links (fix #16) ----------

PAGES = ["/", "/legal-assistant", "/document-generator", "/contract-analyzer",
         "/compliance-checker", "/document-simplifier"]


def internal_links(html):
    """Every same-site href/src on a page (anchors like #features excluded)."""
    return {u.split("#")[0] for u in re.findall(r'(?:href|src)="(/[^"]*)"', html) if u.split("#")[0]}


@pytest.mark.parametrize("page", PAGES)
def test_every_internal_link_on_every_page_works(client, page):
    links = internal_links(client.get(page).text)
    assert links, "expected some links"
    broken = {link: client.get(link).status_code for link in links}
    broken = {link: code for link, code in broken.items() if code != 200}
    assert not broken, f"broken links on {page}: {broken}"


@pytest.mark.parametrize("page", PAGES)
def test_no_links_to_account_pages_that_do_not_exist(client, page):
    html = client.get(page).text
    assert "/login" not in html and "/register" not in html


# ---------- no sideways scrolling on phones (fix #17) ----------

def test_response_area_counts_padding_inside_its_width():
    """width:100% + padding + border made the box 18px wider than a 375px phone screen."""
    block = re.search(r"\.response-area \{([^}]*)\}", STYLES).group(1)
    assert "width: 100%" in block
    assert "box-sizing: border-box" in block


# ---------- result box hidden until there is a result (fix #18) ----------

RESULT_PAGES = ["/contract-analyzer", "/compliance-checker", "/document-generator", "/document-simplifier"]


def test_empty_result_box_is_hidden():
    assert re.search(r"\.response-area:empty \{\s*display: none;\s*\}", STYLES)


@pytest.mark.parametrize("path", RESULT_PAGES)
def test_result_box_starts_truly_empty_and_is_announced(client, path):
    """:empty only matches with no whitespace inside, so the markup must be exactly this."""
    assert '<div id="response" class="response-area" aria-live="polite"></div>' in client.get(path).text


# ---------- no blank gaps in HTML results (fix #19) ----------

def css_rule(css, selector):
    m = re.search(r"^" + re.escape(selector) + r" \{([^}]*)\}", css, re.M)
    return m.group(1) if m else ""


@pytest.mark.parametrize("source", ["static/css/styles.css", "templates/compliance-checker.html",
                                    "templates/contract-analyzer.html", "templates/document-generator.html",
                                    "templates/document-simplifier.html"])
def test_result_container_does_not_preserve_whitespace(source):
    """pre-wrap on the container turned the HTML template's indentation into big blank gaps."""
    assert "white-space" not in css_rule(Path(source).read_text(encoding="utf-8"), ".response-area")


def test_plain_text_answers_keep_their_line_breaks():
    assert "white-space: pre-wrap" in css_rule(STYLES, ".plain-text")
    simplifier = Path("templates/document-simplifier.html").read_text(encoding="utf-8")
    assert simplifier.count('class="simplified-text plain-text"') == 2  # text upload and PDF upload
    generator = Path("templates/document-generator.html").read_text(encoding="utf-8")
    assert "documentText.className = 'plain-text'" in generator
    assert "documentText.textContent = data.response" in generator  # still textContent: no XSS


# ---------- one request at a time (fix #20) ----------

REQUEST_HANDLERS = {  # template -> number of handlers that send a request
    "legal-assistant.html": 1, "document-generator.html": 1, "contract-analyzer.html": 2,
    "compliance-checker.html": 2, "document-simplifier.html": 2,
}


def test_whilebusy_helper_is_defined_in_base():
    base = Path("templates/base.html").read_text(encoding="utf-8")
    assert "function whileBusy(handler)" in base
    assert "finally {" in base.split("function whileBusy(handler)", 1)[1].split("// Mobile menu toggle")[0]


@pytest.mark.parametrize("template, expected", REQUEST_HANDLERS.items())
def test_every_request_handler_is_wrapped_in_whilebusy(template, expected):
    js = (Path("templates") / template).read_text(encoding="utf-8")
    handlers = re.findall(r"addEventListener\('(?:submit|change)', (whileBusy\()?async function", js)
    assert len(handlers) == expected
    assert all(handlers), f"{template}: a submit/upload handler is not wrapped in whileBusy()"


def test_upload_box_looks_disabled_while_busy():
    assert re.search(r'\.chat-input-container\[aria-busy="true"\] \.upload-label \{[^}]*pointer-events: none', STYLES)


# ---------- keep the user's text (fix #21) ----------

@pytest.mark.parametrize("template, var", [("compliance-checker.html", "documentInput"),
                                           ("contract-analyzer.html", "contractInput"),
                                           ("document-generator.html", "promptInput"),
                                           ("document-simplifier.html", "documentInput")])
def test_tool_pages_keep_the_text_after_a_run(template, var):
    js = (Path("templates") / template).read_text(encoding="utf-8")
    assert f"{var}.value = ''" not in js


def test_legal_assistant_gives_the_question_back_after_an_error():
    js = Path("templates/legal-assistant.html").read_text(encoding="utf-8")
    catch_block = js.split("} catch (error) {", 1)[1].split("\n        }\n", 1)[0]
    assert "textarea.value = question" in catch_block


@pytest.mark.parametrize("template", ["legal-assistant.html", "document-generator.html", "contract-analyzer.html",
                                      "compliance-checker.html", "document-simplifier.html"])
def test_every_page_shows_the_servers_own_error_message(template):
    js = (Path("templates") / template).read_text(encoding="utf-8")
    assert "throw await errorFromResponse(response)" in js
    assert "Server error:" not in js and "HTTP error!" not in js


# ---------- shared, keyboard-accessible PDF upload box (fix #22) ----------

UPLOAD_PAGES = {"/contract-analyzer": "Upload PDF Contract",
                "/compliance-checker": "Upload PDF Document",
                "/document-simplifier": "Upload PDF Document"}


@pytest.mark.parametrize("path, label", UPLOAD_PAGES.items())
def test_upload_box_has_a_visible_icon_and_label(client, path, label):
    html = client.get(path).text
    section = html.split('<div class="upload-section">', 1)[1].split('<form', 1)[0]
    assert "<svg" in section            # inline icon, needs no icon font
    assert f"<span>{label}</span>" in section
    assert 'class="fas' not in html     # Font Awesome is never loaded


@pytest.mark.parametrize("path", UPLOAD_PAGES)
def test_upload_input_stays_keyboard_reachable(client, path):
    html = client.get(path).text
    assert '<input type="file" id="pdfUpload" class="file-input"' in html
    assert 'style="display: none' not in html
    # input before its label, so the focus ring selector can work
    assert html.index('id="pdfUpload"') < html.index('for="pdfUpload"')


def test_upload_css_hides_input_visually_but_not_from_keyboard():
    block = css_rule(STYLES, ".file-input")
    assert "opacity: 0" in block and "display: none" not in block
    assert ".file-input:focus-visible + .upload-label" in STYLES


@pytest.mark.parametrize("template", ["contract-analyzer.html", "compliance-checker.html", "document-simplifier.html"])
def test_pages_do_not_carry_their_own_upload_css(template):
    css = (Path("templates") / template).read_text(encoding="utf-8")
    assert not re.search(r"^\.(upload-|file-input|file-info)", css, re.M)


# ---------- small cleanups: items 4-10 (fix #23) ----------

BASE = Path("templates/base.html").read_text(encoding="utf-8")


def test_base_html_has_no_style_blocks_and_ends_at_html():
    """Navbar CSS used to sit in a <style> block after </html>; colours in a second inline :root."""
    assert "<style" not in BASE
    assert BASE.rstrip().endswith("</html>")
    assert ".navbar {" in STYLES and ".mobile-menu.active" in STYLES


@pytest.mark.parametrize("page", PAGES)
def test_every_page_has_exactly_one_main(client, page):
    html = client.get(page).text
    assert html.count("<main") == 1 and html.count("</main>") == 1


def test_mobile_menu_button_is_accessible():
    assert 'aria-label="Open menu" aria-expanded="false" aria-controls="mobile-menu"' in BASE
    assert 'id="mobile-menu"' in BASE
    assert "setAttribute('aria-expanded', String(open))" in BASE
    assert "e.key === 'Escape'" in BASE


def test_one_root_block_defines_every_colour_the_site_uses():
    assert STYLES.count(":root {") == 1
    root = css_rule(STYLES, ":root")
    used = set(re.findall(r"var\((--[\w-]+)\)", STYLES))
    for template in Path("templates").rglob("*.html"):
        used |= set(re.findall(r"var\((--[\w-]+)\)", template.read_text(encoding="utf-8")))
    defined = set(re.findall(r"(--[\w-]+):", root))
    assert used <= defined, f"used but never defined: {used - defined}"  # --accent-color was missing
    assert "--primary-color: #4CAF50" in root  # the green the site really shows


def test_loading_box_counts_padding_inside_its_width():
    assert "box-sizing: border-box" in css_rule(STYLES, ".loading")


@pytest.mark.parametrize("template", sorted(Path("templates").rglob("*.html")), ids=lambda p: p.name)
def test_no_local_variable_shadows_the_global_document(template):
    assert not re.search(r"\b(const|let|var)\s+document\s*=", template.read_text(encoding="utf-8"))


@pytest.mark.parametrize("template", ["contract-analyzer.html", "compliance-checker.html"])
def test_no_dead_error_field_branches(template):
    js = (Path("templates") / template).read_text(encoding="utf-8")
    assert "data.error" not in js and "raw_response" not in js


# ---------- one shared stylesheet (fix #25) ----------

SHARED_SELECTORS = [".chat-container", ".chat-main", ".chat-messages", ".welcome-message", ".chat-input-container",
                    ".chat-form", ".input-wrapper", ".send-button", ".disclaimer", ".response-area",
                    ".result-section", ".error-message"]


@pytest.mark.parametrize("template", ["document-generator.html", "contract-analyzer.html",
                                      "compliance-checker.html", "document-simplifier.html"])
def test_tool_pages_have_no_copied_css(template):
    assert "{% block extra_css %}" not in (Path("templates") / template).read_text(encoding="utf-8")


@pytest.mark.parametrize("selector", SHARED_SELECTORS)
def test_shared_rules_are_defined_once_in_styles_css(selector):
    assert len(re.findall(r"(?m)^" + re.escape(selector) + r" \{", STYLES)) == 1
    for template in Path("templates").rglob("*.html"):
        assert not re.search(r"(?m)^" + re.escape(selector) + r" \{", template.read_text(encoding="utf-8")), template.name


def test_legal_assistant_only_keeps_its_own_components():
    css = Path("templates/legal-assistant.html").read_text(encoding="utf-8").split("{% block extra_css %}")[1].split("{% endblock %}")[0]
    selectors = {s.strip() for s in re.findall(r"(?m)^([.\w][^{\n]*) \{", css)}
    assert selectors <= {".chat-sidebar", ".new-chat", ".new-chat-btn", ".new-chat-btn:hover", ".plus-icon",
                         ".chat-history", ".message", ".message.user", ".message.assistant", ".message-content",
                         ".message-text"}, selectors


@pytest.mark.parametrize("template, old", [("contract-analyzer.html", "analysis-"), ("compliance-checker.html", "compliance-")])
def test_both_result_pages_use_the_same_result_classes(template, old):
    js = (Path("templates") / template).read_text(encoding="utf-8")
    assert 'class="result"' in js and 'class="result-section"' in js
    assert f'class="{old}' not in js


# ---------- global CSS must not override page components (fix #15) ----------

def test_no_global_rules_that_restyle_every_textarea_or_submit_button():
    """These made the send button a 180px block and the chat textarea 150px tall."""
    assert 'button[type="submit"]' not in STYLES
    for block in re.findall(r"(?:^|\n)textarea[^{]*\{([^}]*)\}", STYLES):
        assert not re.search(r"\b(width|min-height|height|border|background|padding)\s*:", block), block


@pytest.mark.parametrize("unused", [".container", ".card", ".form-group", ".instructions", ".error {", ".hero"])
def test_leftover_rules_from_the_deleted_page_are_gone(unused):
    assert unused not in STYLES


def test_chat_input_shows_keyboard_focus():
    assert re.search(r"\.input-wrapper:focus-within \{[^}]*border-color", STYLES)


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
