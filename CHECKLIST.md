# Project Checklist

Tracks every fix and feature. Each item is done in its own commit.

**Legend:** `[x]` done · `[ ]` to do · `[~]` in progress · `[?]` needs a decision

---

## Open decisions

- [?] **Brand name**: pick one of LegalEdge AI / AutoDocuGen AI Suite / CaseWise AI (currently mixed across pages)
- [?] **LLM provider for the deployed version**: keep Ollama only (self-hosted) or add a hosted API option
- [?] **Target jurisdiction(s)** for compliance checks (e.g. India, US, UK, EU)

---

## Phase 0: Critical fixes and cleanup

### Setup
- [x] Initialise git repo, add CHECKLIST.md, baseline commit of existing code
- [x] Push to GitHub: https://github.com/Anjali2034/Casewise-AI
- [ ] Add `.gitattributes` so line endings are consistent (removes the LF/CRLF warnings)
- [ ] Add README.md and `.env.example`

### Security and correctness
- [x] Fix XSS: escape LLM output and error messages before inserting into the page (contract analyzer, compliance checker, simplifier)
- [x] Stop blocking the server: make LLM endpoints non-blocking (`def` endpoints or async HTTP client)
- [x] Don't crash on startup when Ollama is unreachable; add a `/health` endpoint
- [x] Fix 400 errors being returned as 500 (empty text, unreadable PDF); pages now show the server's error message
- [x] Return proper HTTP errors from contract analysis and compliance check (no 200 responses with an `error` field)
- [ ] Remove the now-unused `if (data.error)` branches in the contract-analyzer and compliance-checker templates
- [x] Fix retry logic: no duplicated text after a partial stream, no retrying 4xx errors, fail fast when Ollama is down (was ~10s before the 503)
- [ ] Make the model name configurable (env var) and make the error messages match it
- [ ] Stop logging full documents, prompts and streamed chunks; remove `console.log` of user text
- [ ] Don't leak internal error details to users
- [ ] Add input limits: max text length, max PDF size, server-side PDF type check
- [ ] Tighten CORS settings (no `*` with credentials)

### Honesty and legal
- [ ] Remove the fake testimonials and the "Trusted by" / "thousands of professionals" claims
- [ ] Add a clear "not legal advice" disclaimer to every tool; fix the Legal Assistant system prompt
- [ ] Use one brand name everywhere (blocked on decision above)

### UI bugs
- [ ] Legal Assistant: make the loading indicator visible
- [ ] Define `--accent-color` (New Chat button hover)
- [ ] Contract Analyzer: replace the Font Awesome icon (FA isn't loaded) with an inline SVG
- [ ] Remove or disable the Sign In / Get Started links (`/login` and `/register` give 404)
- [ ] Move the stray `<style>` block in base.html inside `<head>`
- [ ] Disable the send button while a request is running (prevents double submits)
- [ ] Keep the user's input after a successful run
- [ ] Remove conflicting colour variables between styles.css and base.html

### Dead code and dependencies
- [ ] Delete the unused root `index.html`
- [ ] Delete the unused `static/js/main.js`
- [ ] Remove unused auth packages (python-jose, passlib, bcrypt)
- [ ] Replace PyPDF2 with pypdf; upgrade python-multipart (CVE fix)
- [ ] Update Python version in render.yaml (3.8 is end-of-life)
- [~] Merge the 3 copy-pasted PDF endpoints into one helper (PDF reading now shared via `extract_pdf_text()`; endpoints still separate)
- [ ] Move the CSS that's repeated in every template into styles.css
- [ ] Move or clean up notebook.ipynb (its PyMuPDF dependency isn't in requirements.txt)

---

## Phase 1: Foundations

- [ ] Stream responses to the browser (SSE) instead of waiting for the full answer
- [ ] Use Ollama JSON mode (`format: "json"`) for contract analysis and compliance
- [ ] Handle long documents: chunking and a warning when text is truncated
- [ ] Low temperature and a larger context window (`num_ctx`) for legal tasks
- [ ] Choice of LLM provider (Ollama / hosted API) so the deployed version works
- [ ] Render markdown in responses; add copy buttons
- [ ] Enter to send, Shift+Enter for a new line
- [ ] Tests (pytest with the LLM mocked out) and linting (ruff)
- [ ] Working deployment on Render

## Phase 2: Core features

- [ ] Chat with your document: Q&A over an uploaded contract that quotes the exact clause
- [ ] Clause-level risk scoring (High/Med/Low, exact text, explanation, suggested rewrite)
- [ ] Jurisdiction-specific compliance checks against a chosen framework, shown as a checklist
- [ ] Template-based document generator (NDA, rental, employment, POA) with forms
- [ ] Export results to DOCX/PDF
- [ ] Legal Assistant conversation memory and real chat history

## Phase 3: Advanced

- [ ] Contract comparison (diff two versions, explain changes in meaning)
- [ ] Extract key dates and obligations, with calendar export
- [ ] OCR for scanned PDFs; DOCX and image uploads
- [ ] Side-by-side original vs simplified text; simplification into other languages
- [ ] User accounts and a document library
- [ ] Case-law and statute lookup from a real data source (no citations from the model's memory)

---

## Changelog

| # | Change | Commit |
|---|--------|--------|
| 1 | Baseline: existing code, checklist, .gitignore | `734b44a` |
| 2 | Fix XSS: shared `escapeHtml()` in base.html, used on every `innerHTML` insert | `ce1dc65` |
| 3 | Non-blocking LLM endpoints (`async def` → `def`); a page load during a slow LLM call went from 2.54s to 0.03s | `2508918` |
| 4 | App starts without Ollama; `GET /health` endpoint; connection failures return 503 instead of a raw 500 | `6582157` |
| 5 | 400s no longer become 500s; shared `extract_pdf_text()` (invalid PDF returns 400); PDF endpoints return 503 when Ollama is down; pages show the server's error message | `9d54add` |
| 6 | Analysis and compliance stop returning 200 on failure: Ollama down returns 503, unparseable model output returns 502 (`LLMResponseFormatError`) | `e42eca5` |
| 7 | Retry policy: 1 retry, transient errors only, never after text has been yielded; real streaming (`stream=True`); typed errors (404 returns 503 with the correct model name, timeout returns 504, stream errors no longer become an empty 200) | _(your commit)_ |
