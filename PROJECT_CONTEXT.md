# CaseWise: project context and handoff

> **For an AI assistant (or developer) picking this project up.** Read this file first. It explains what CaseWise is, how the owner wants work done, what is finished, what remains, how to deploy, and the facts and pitfalls already discovered, so you don't have to rediscover them.
>
> **Last updated:** 2026-11-04 · **Latest commit:** `86193a2` · **Commits:** 41 · **Tests:** 643 passing
>
> Keep this file current: update it whenever a milestone is finished, a decision is made, or a new fact is learned.

---

## 1. What CaseWise is

CaseWise is a web app that helps people in **India** understand and draft legal documents in plain language. It has five AI tools:

| Tool | Page | What it does |
|---|---|---|
| Legal Assistant | `/legal-assistant` | Chat: answers legal questions and remembers the conversation |
| Document Generator | `/document-generator` | Drafts a document (NDA, lease…) with `[PLACEHOLDERS]` for missing details |
| Contract Analyzer | `/contract-analyzer` | Key clauses, potential risks, summary (paste text or upload a PDF) |
| Compliance Checker | `/compliance-checker` | Missing clauses, compliance issues, recommendations, under Indian law |
| Document Simplifier | `/document-simplifier` | Rewrites legal text in plain English (text or PDF) |

It gives **general legal information, not legal advice**. That is stated on every page and in every AI prompt.

**Stack:** Python 3.13 · FastAPI · Jinja2 templates · vanilla JavaScript (no build step) · AI via **Groq** (hosted, live site) or **Ollama** (local) · hosted on **Render** (free plan).

**Repository:** https://github.com/Anjali2034/CaseWise (branch `main`). The old name, `Casewise-AI`, redirects here.

**Current status:** **live at https://casewise-youo.onrender.com** (Render free plan, deployed from `86193a2`). Automated live checks passed; the owner's real-phone and rate-limit checks remain (section 9.1). The contact email and lawyer review are deliberately put off (see section 9.1). See [section 9](#9-what-remains).

---

## 2. How the owner wants work done (follow these rules)

1. **Never commit or push yourself.** When a change is ready, give the owner the exact commands (`git add …`, `git commit -m "…"`, `git push`) and wait for "done". Commits are made under the owner's own GitHub identity.
2. **No AI co-author lines** in commit messages (no `Co-Authored-By: Claude`). The owner explicitly does not want them.
3. **Group related items into one commit** (e.g. "streaming + Markdown + JSON mode"), one clear theme per commit.
4. **Run the full test suite before handing over any commit**, and report the result. All tests must pass: `venv\Scripts\python -m pytest`.
5. **Add tests for every change.** When a change is visual, also check it with screenshots on desktop **and** phone (see [section 7](#7-testing)).
6. **`CHECKLIST.md` is the owner's private progress tracker.** It is git-ignored: never `git add` it, but keep it updated (tick items, add a changelog row per commit).
7. **Explain in plain language.** The owner is building this product and wants to understand what changed and why. Show results (numbers, before/after), and be honest about anything unverified or any mistake made.
8. **Ask before big product or design changes.** For design, show options (screenshots) first. Design direction B was chosen this way.
9. **Logic and structure changes are welcome** when there is a cleaner or more robust way.
10. **Secrets:** never ask the owner to paste the Groq API key into the chat, and never print it. It lives only in `.env` (git-ignored) locally, and in Render's secret settings when deployed.

---

## 3. Decisions already made (don't re-ask)

| Topic | Decision |
|---|---|
| Brand | **CaseWise**, spelled exactly like that, everywhere. A test (`tests/test_branding.py`) fails if either of the two earlier product names, or a misspelling, appears anywhere in the project (including this file) |
| Jurisdiction | **India.** Prompts default to Indian law; mention when rules vary by state (stamp duty, rent rules). Keep the design open to more countries later |
| AI provider (live) | **Groq**, model **`openai/gpt-oss-120b`**. Chosen because Groq's Services Agreement says it doesn't train on API inputs/outputs and keeps no inference data by default. **Zero Data Retention (Global ZDR) is turned on** in the owner's Groq console |
| AI provider (local) | **Ollama** (free, private), default model `llama2` |
| Hosting | **Render free plan** (accepted: it sleeps after ~15 min idle, and the next visit takes 30-60 s to wake) |
| Design | **"Ivory & Ink" (direction B)**: ivory `#faf8f4`, ink `#1b2230`, teal `#0f766e`; headings in Source Serif 4, text in Inter (both self-hosted) |
| Data storage | **No database.** Documents are processed in memory; chats are saved only in the user's browser (`localStorage`) |
| Accounts | None for v1 |

---

## 4. Architecture

### File map

```
main.py                 FastAPI app: settings, pages, API endpoints, input limits, rate limiting, error mapping
llm_client.py           AI client: LLMClient base (tools, retries, timeouts, health cache) + OllamaClient + GroqClient
prompts.py              System prompt per tool + shared SAFETY_RULES (not legal advice, Indian law, never invent laws)
rate_limit.py           Per-visitor RateLimiter (per minute / per day, in memory) + spoof-safe client_key()
security.py             SecurityHeadersMiddleware (pure ASGI): CSP with a per-request nonce, other security headers, HSTS over HTTPS
templates/base.html     Layout, navbar, and ALL shared JavaScript helpers (see below)
templates/home.html     Landing page (has its own page CSS)
templates/legal-assistant.html   Chat page: saved chats (localStorage), phone chats panel, memory
templates/<tool>.html   The other four tool pages
templates/privacy.html, terms.html, error.html   Policies (dated 4 November 2026) and the 404/500 page
templates/partials/     disclaimer.html ("Not legal advice" macro), upload.html (PDF upload box), footer.html, contact.html (contact_line macro)
static/css/styles.css   All shared styles. Design tokens (colours, fonts) are in the single :root block at the top
static/fonts/           Inter + Source Serif 4 (woff2, Latin subsets) + their OFL licences; fonts.css
static/vendor/          marked 18.1.1 (Markdown) + DOMPurify 3.4.17 (sanitising), with licences
static/favicon.svg, favicon-32.png, apple-touch-icon.png, og-image.png (1200x630 link preview)
tests/                  643 pytest tests (no real AI needed; see section 7)
requirements.in         Direct dependencies (EDIT THIS)    -> requirements.txt (generated lock, hashed)
requirements-dev.in     Test tools (EDIT THIS)             -> requirements-dev.txt (generated lock)
render.yaml             Render Blueprint (service "casewise", free plan, Groq, health check)
.env.example            Every setting, documented. Copy to .env (git-ignored)
notebooks/              An early experiment, not used by the app
```

### How a request flows

1. A page (e.g. Contract Analyzer) sends `fetch('/analyze-contract', …)`. The text tools use the **streaming** endpoints (`/…/stream`) through `streamInto()`.
2. FastAPI validates the body with Pydantic: text is trimmed, must not be empty, and is at most `MAX_TEXT_CHARS`. Invalid input gets a readable **400/413** (a custom handler replaces FastAPI's 422).
3. The `ai_rate_limit` dependency checks the visitor's per-minute and per-day allowance (**429** + `Retry-After`).
4. **Every** endpoint calls `run_tool()` (whole answer) or `stream_tool()` (streams NDJSON). Both turn AI errors into HTTP statuses in **one place** (`handle_llm_error`).
5. `llm` (an `OllamaClient` or `GroqClient`, chosen by `LLM_PROVIDER`) builds the request, streams the answer, retries transient errors **once, only before any text has arrived**, and raises typed errors.

### Error mapping (`handle_llm_error` in main.py)

| Error (llm_client.py) | HTTP | Message to user |
|---|---|---|
| bad input / too long (validation) | 400 / 413 | e.g. "The text cannot be empty." |
| `LLMRateLimitedError` (provider quota) | 429 | "CaseWise is very busy right now…" (+ Retry-After) |
| `DocumentTooLongError` | 413 | "This document is too long for the AI…" |
| `LLMResponseFormatError` / empty answer / other `LLMError` | 502 | "…unexpected format" / "…empty answer" / "…returned an error" |
| `ServiceUnavailableError`, `ModelNotFoundError` | 503 | provider-specific safe message (Ollama: "start Ollama"; Groq: neutral) |
| `LLMTimeoutError` | 504 | "…took too long…" |
| anything else | 500 | generic message **with an error ID**; the full trace is logged under that ID |

A request that accepts HTML gets `templates/error.html` for 404, 405 and 500 errors. Everything else, including API calls, gets JSON.

### Shared JavaScript helpers (templates/base.html)

| Helper | Purpose |
|---|---|
| `escapeHtml()` | Escape text before any `innerHTML` (XSS) |
| `renderMarkdown()` | marked → DOMPurify, with `style` attributes and form tags forbidden. **All AI output that becomes HTML goes through this** |
| `streamInto(area, url, options, hooks)` | Reads the NDJSON stream, redraws at most once per frame, `onFirstText` / `onUpdate` hooks, returns the raw Markdown |
| `showError(area, message)` | Red error box built with `textContent` |
| `errorFromResponse()` | Turns a failed response into an `Error` using the server's `detail` |
| `whileBusy(handler)` | One request at a time: locks the tool's input area (`aria-busy`), unlocks in `finally` |
| `addResultActions(el, {name, download})` | Copy (HTML + plain text) and Download (.txt with a not-legal-advice footer, local date) |
| keyboard handler | Ctrl/Cmd+Enter sends everywhere; plain Enter only on forms with `data-enter-sends` (the chat); ignored while an input method (e.g. Hindi) is composing |

### Settings (environment variables; all documented in `.env.example`)

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama` or `groq`. Render uses `groq` |
| `GROQ_API_KEY` | (empty) | **Secret.** `.env` locally, Render dashboard when deployed |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | |
| `OLLAMA_API_BASE` | `http://localhost:11434` | |
| `OLLAMA_MODEL` | `llama2` | |
| `OLLAMA_NUM_CTX` | `4096` | Ollama context size; longer text is refused with 413 instead of silently cut |
| `MAX_TEXT_CHARS` | 20000 (groq) / 50000 (ollama) | Groq's free plan allows 8,000 tokens per minute, so one request must fit in that |
| `MAX_PDF_MB` | `10` | |
| `AI_REQUESTS_PER_MINUTE` / `AI_REQUESTS_PER_DAY` | `5` / `50` | Per visitor; `0` disables |
| `TRUST_PROXY_HEADERS` | `false` | `true` on Render, so rate limits use the visitor's IP |
| `ALLOWED_ORIGINS` | (empty) | CORS off by default (the pages are same-origin) |
| `CONTACT_EMAIL` | (empty) | Shown on Privacy and Terms. While empty, they say contact details are coming and the server logs a warning. **Must be set before launch** |
| `SITE_URL` | `RENDER_EXTERNAL_URL` | Public address for link previews. Only needed for a custom domain |
| `PORT` | `8000` | |

---

## 5. Invariants: things that must stay true

Tests guard almost all of these. If one of them fails, fix the code, not the test.

- **XSS:** AI output reaches the page only via `renderMarkdown()`, `escapeHtml()` or `textContent`. Raw `${value}` is only allowed on text-only APIs (`textContent`, `className`, `setAttribute`, `confirm`, `.download`).
- **Privacy:** logs record sizes ("212 chars"), never questions, documents, prompts, answers or file names. No `console.log` in templates. No database. Chats stay in the browser.
- **Honesty:** no fabricated testimonials, users or claims. Privacy statements must match reality (e.g. "Nothing Stored on Our Servers… chats are saved only in your own browser").
- **Policies:** `/privacy` and `/terms` must describe what the code really does. Change them, and their date, whenever data handling changes (a new provider, analytics, cookies, a database).
- **CSP:** no `'unsafe-inline'` or `'unsafe-eval'`. Every inline `<script>` and `<style>` carries `nonce="{{ request.state.csp_nonce }}"`. No `style="..."` attributes or inline `onclick=` handlers, because the browser blocks them. Outside images are blocked, so an AI answer can't leak data through an image URL.
- **Every tool page** shows the shared "Not legal advice" notice; **every prompt** ends with `SAFETY_RULES`.
- **Errors** never expose internal details (an error ID instead); the API key never appears in messages or logs.
- **Brand:** only "CaseWise".
- **Design:** colours only from the `:root` tokens; all text meets WCAG AA (at least 4.5:1, tested); no `rgba(255,255,255,…)` dark-theme leftovers; fonts self-hosted (no Google Fonts requests).
- **Layout:** the four non-chat tools use `.tool-page` (the page scrolls as one); the chat page keeps its fixed layout.

---

## 6. Running it locally (Windows / PowerShell)

```powershell
python -m venv venv                      # once (already exists on the owner's machine)
.\venv\Scripts\Activate.ps1              # if blocked: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
pip install -r requirements-dev.txt
uvicorn main:app --reload                # http://localhost:8000  ·  /health  ·  /docs
pytest                                   # 643 tests, about 25 s
```

The owner's `.env` already has `LLM_PROVIDER=groq` and a working `GROQ_API_KEY` (ZDR on). `.env` changes need a full restart (Ctrl+C); `--reload` doesn't pick them up.

---

## 7. Testing

- **pytest (643 tests)**, grouped by file: pages/health, API endpoints and status codes, Groq client (fake OpenAI-compatible server), Ollama retries (fake server), rate limits, CORS, streaming + Markdown + JSON mode + India prompts, memory + Copy/Download + keyboard, privacy (a secret marker never reaches logs), XSS guards, JS syntax (esprima), UI rules, branding, design (contrast maths, fonts, icons, layout), launch basics (policies, link previews, error pages, security headers).
- `tests/conftest.py` pins every setting (so a local `.env` can't affect results), points Ollama at a closed port, disables rate limits (tests in `test_rate_limit.py` re-enable them) and provides `llm_reply()` to fake the model.
- **Browser checks** were done with headless Chrome driven over the DevTools protocol (a small Python + `websocket-client` script, kept outside the repo). Useful lessons:
  - Chrome on Windows won't make a window narrower than about 490 px. For phone views, use DevTools `Emulation.setDeviceMetricsOverride` (390×844), or load the page in a 375 px iframe.
  - `--virtual-time-budget` can stall on streamed responses, and `--timeout` stops page loading; use DevTools polling in real time instead.
  - A test script injected into the page must wait for `load` before submitting, or the form does a native submit and reloads.
  - Wait for `document.fonts.ready` and for CSS transitions before taking screenshots.
- There is **no CI yet** (planned: GitHub Actions running pytest on every push).

---

## 8. What is done

**Phase 0: fixes and cleanup** (commits `ce1dc65` … `53c2856`):
- XSS fixed.
- The server no longer blocks: endpoints are sync `def` and run in a thread pool.
- Starts without the AI service; `/health` added.
- Correct HTTP errors everywhere.
- Safe retries, and real streaming from the provider.
- Configurable model.
- No user content in logs.
- No internal details in errors.
- Input limits.
- CORS closed by default.
- Dependencies upgraded (CVE fixes), PyPDF2 replaced by pypdf, and a hashed lock file (pip-tools).
- Python 3.13.5.
- Fake testimonials removed.
- "Not legal advice" notice everywhere, and prompts centralised in `prompts.py`.
- UI bugs fixed: spinners, broken global CSS, dead links, phone overflow, blank gaps, double submits, kept input, keyboard-accessible upload, menu accessibility.
- CSS consolidated into `styles.css`.
- All 8 endpoints go through `run_tool()`.
- Complete rebrand to CaseWise.
- README, `.gitattributes` (LF line endings).

**v1 launch groups:**
- **Group 1 (`47bcab4`, `df830b7`): L1, L2.**
  - Groq provider (`LLMClient` base class, typed errors, health check cached 60 s, temperature 0.2).
  - Per-visitor rate limits.
  - `render.yaml` for the free plan.
  - Model switched to `gpt-oss-120b`, and the Groq text limit set to 20k characters.
- **Group 2 (`818aef0`): L4–L7.**
  - Streaming answers (NDJSON, `/stream` endpoints).
  - Safe Markdown (marked + DOMPurify, bundled in the repo).
  - JSON mode for the analysis tools.
  - Indian law by default.
- **Group 3 (`633b625`): L8, L9, L11, L12.**
  - Legal Assistant memory (up to 20 earlier messages; server validates roles and trims the oldest).
  - Browser-saved chats.
  - Ollama context check.
  - Copy/Download.
  - Enter to send.
- **Group 4 (`0a57f85`): L10, the design pass "Ivory & Ink".**
  - Colour tokens, self-hosted fonts, logo and favicons.
  - Redesigned home page (line icons, clickable 3 + 2 tool cards, honest privacy section).
  - Single-scroll tool pages and a phone chats panel.
  - Current-page marker in the navbar, focus rings, fade-in that respects reduced motion.
  - WCAG AA contrast tested.
- **Group 5 (`86193a2`): L3, L13.**
  - Privacy Policy and Terms of Use, with a footer and a short notice on each tool saying where text goes.
  - Page descriptions and link previews (Open Graph, Twitter, a 1200x630 image).
  - Designed 404/500 pages for browsers; API clients still get JSON.
  - Security headers with a strict nonce-based CSP. Checked in Chrome with live Groq: 0 violations on all pages and tools, and injected scripts, styles and outside images were all blocked.
  - Put off by the owner: the contact email and a lawyer review.

**Live-verified with Groq:** all five tools answer in about 1–2.5 s; streaming shows first words in about 1 s; JSON mode returned 6/6 valid; memory follow-ups work; PDF upload works; the key never appears in logs.

---

## 9. What remains

### 9.1 Necessary before going live (in this order)

**Put off by the owner (2026-11-04), not blocking the deploy:** the owner has no public email yet, and CaseWise is a project for now. The pages say contact details are coming, and the server logs a warning. Before promoting the site widely: set `CONTACT_EMAIL` in Render's Environment tab (no code change; don't publish a personal address without asking), and have a lawyer review `/privacy` and `/terms`.

**L14: Deploy to Render and verify live.** Deployed 2026-11-04. Passed on the live site: `/health` ok; all pages, assets, security headers and HTTPS redirect; all five tools by text; three PDF tools; streaming (first words in 1.4 s); bad-input errors; no CSP violations or sideways scrolling in Chrome (desktop, and the home page at phone size); a limit of 5 a minute; faked `X-Forwarded-For` and `True-Client-IP` don't bypass it, and Cloudflare answers a faked `CF-Connecting-IP` with 403. **Still open from the list below:** that two different networks get separate limits, the wake-up after sleep, a real phone, and the WhatsApp preview. Original checklist:
- Open `/health` → `"status":"ok"`, `"provider":"groq"`.
- Run every tool once on the live URL (text and PDF).
- **Confirm which IP header Render sends.** `rate_limit.client_key()` prefers `True-Client-IP` / `CF-Connecting-IP`, otherwise the **last** `X-Forwarded-For` entry. Community reports say Render appends to client-sent X-Forwarded-For (not officially documented). Check by temporarily logging which of these headers are present (names only, never values in permanent logs), or test from two different networks or devices that limits are separate, and confirm a spoofed `X-Forwarded-For` doesn't bypass the limit. Remove any temporary logging afterwards.
- Check behaviour after the free-plan sleep (first visit about 30–60 s). Optionally show a friendly "waking up" message.
- Try it on a real phone.

**Owner's own check (no code):** with the real model, try each tool once and confirm answers say "not legal advice" where relevant, use placeholders in drafts, and don't invent laws or case citations. Groq results so far looked good, but the model can still be wrong (e.g. it once claimed an 11-month lease needs a registration clause).

### 9.2 Optional, soon after launch

- **GitHub Actions:** run `pytest` on every push (install from `requirements-dev.txt`, Python 3.13).
- **Linting:** ruff.
- **Custom domain** (e.g. casewise.in): set `SITE_URL`. HSTS is already sent over HTTPS (one year, without `includeSubDomains`).
- **Privacy-friendly analytics** (no cookies); **error alerts** on 500s.
- **Example inputs** on each tool ("Try a sample NDA").
- **A "waking up" message** for the free-plan cold start.
- **`robots.txt` and `sitemap.xml`.**
- **Rename** the GitHub repo to `CaseWise`, then update the two clone lines in README.md and the git remote.
- **Move inline page JavaScript** into `static/js/` (helps testing; the CSP already works through nonces).

### 9.3 Future features (product roadmap)

- **Chat with your document:** questions about an uploaded contract, quoting the exact clause.
- **Clause-level risk scoring:** High / Medium / Low, exact text, explanation, suggested rewrite.
- **Compliance checklists** against chosen Indian frameworks (e.g. Indian Contract Act, data protection).
- **A template-based Document Generator** with forms (NDA, rental, employment, power of attorney).
- **Export** to DOCX / PDF.
- **Contract comparison:** diff two versions and explain the changes in meaning.
- **Key dates and obligations** extraction, with calendar export.
- **OCR for scanned PDFs;** DOCX and image uploads.
- **Long-document chunking** (beyond Groq's per-request limit).
- **Side-by-side original vs simplified;** simplification into Hindi and other Indian languages. Inter covers Latin only; Devanagari currently uses system fonts, so self-host Noto Sans Devanagari if Hindi is added.
- **User accounts** and a document library. These would need a database, a new privacy review and updated privacy statements.
- **Case-law and statute lookup** from a real data source. Never rely on the model's memory for citations.

### 9.4 Known limitations and risks

- **Groq free plan** (read from response headers): **1,000 requests a day and 8,000 tokens a minute, shared by all visitors.** A typical request is 400–900 tokens. When it's exhausted, users see a 429 "very busy". If usage grows, consider Groq's paid Developer plan, check its terms first, and keep "no training" and ZDR.
- **Rate limits are in memory:** they reset when the free-plan server restarts or sleeps, and assume a single instance. Multiple instances would need a shared store (e.g. Redis).
- **The AI can be wrong** about the law. Disclaimers and lawyer recommendations stay.
- **Scanned (image-only) PDFs** aren't supported yet (clear 400 message).
- **Groq model availability changes.** Llama models disappeared from the free plan once already. If `/health` shows `model_available: false`, list the models available to the key (`GET https://api.groq.com/openai/v1/models`) and pick a current one with a large context.

---

## 10. Deploy process (Render)

The service is fully described in **`render.yaml`**: web service `casewise`, `runtime: python`, `plan: free`, build `pip install -r requirements.txt`, start `uvicorn main:app --host 0.0.0.0 --port $PORT`, `healthCheckPath: /health`, `PYTHON_VERSION=3.13.5`, `LLM_PROVIDER=groq`, `GROQ_MODEL=openai/gpt-oss-120b`, `TRUST_PROXY_HEADERS=true`, limits 5/min and 50/day, `MAX_TEXT_CHARS=20000`, and `GROQ_API_KEY` and `CONTACT_EMAIL` marked `sync: false` (entered in Render, not stored in the repo).

**First deploy:**
1. Make sure everything is committed and pushed to `main` on GitHub, and that `pytest` passes.
2. **Groq:** the owner already has an account and a key, with **Global ZDR enabled** (console.groq.com → Settings → Data Controls). Consider creating a **separate key** just for production (console.groq.com/keys), so the local and live keys can be revoked independently.
3. **Render:** sign in at render.com with GitHub → **New → Blueprint** → choose the `CaseWise` repository → Render reads `render.yaml`.
4. When Render asks for **`GROQ_API_KEY`**, paste the key. Leave **`CONTACT_EMAIL`** empty until the owner has an address. Later Blueprint syncs ignore `sync: false` values, so add it in the **Environment** tab. Render only asks for `sync: false` values when the Blueprint is first created; later changes go in the service's **Environment** tab.
5. Deploy. The build installs the hashed lock file. Watch the logs for `AI ready: groq (model: openai/gpt-oss-120b)`.
6. Open `https://<service-name>.onrender.com/health`. Expect `{"status":"ok","provider":"groq",…,"reachable":true,"model_available":true}`. The exact URL depends on name availability (e.g. `casewise.onrender.com` or with a suffix).
7. Run the L14 checks ([section 9.1](#91-necessary-before-going-live-in-this-order)).

**Updating later:** push to `main`. Render auto-deploys, and the health check must pass before it switches traffic.

**Rollback:** in the Render dashboard → the service → **Events / Deploys** → redeploy an earlier deploy. Or `git revert` the bad commit and push.

**Changing settings:** Render dashboard → service → **Environment**. For example, raise `AI_REQUESTS_PER_DAY`, or change `GROQ_MODEL` if Groq retires the model.

**If `/health` is "degraded":**

| `/health` shows | Fix |
|---|---|
| `reachable: false` | The key is missing or wrong: check `GROQ_API_KEY` in Render |
| `model_available: false` | Groq renamed or retired the model: pick a current one and set `GROQ_MODEL` |

**Updating dependencies:** edit `requirements.in` (or `requirements-dev.in`), then:

```powershell
pip-compile --generate-hashes requirements.in
pip-compile --generate-hashes --allow-unsafe requirements-dev.in
pytest
```

Commit both the `.in` and `.txt` files. `--allow-unsafe` is needed for the dev lock, because pip-tools depends on setuptools.

---

## 11. Facts and pitfalls already discovered

- **Groq:**
  - `llama-3.3-70b-versatile` and other Llama chat models are **not available** on the owner's free plan. Available chat models with a 131k context: `openai/gpt-oss-120b` (chosen: most accurate in a live legal test), `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`. All have the same free quota.
  - The `reasoning_effort: "low"` setting saved only 10–40 % of tokens and gave a slightly wrong legal answer, so the default is used.
  - JSON mode (`response_format: json_object`) works **with streaming**.
  - Errors: 401 means a bad key (shown as "isn't configured correctly"), 429 a rate limit, and a 400 containing `context_length` means the text is too long.
- **Render:**
  - The live service sits behind Cloudflare (`Server: cloudflare`). Don't send faked `CF-Connecting-IP` headers when testing: Cloudflare returns 403, and the next connection from that machine was reset.
  - The rate limit runs before text checks, so empty or too-long requests also count towards it.
  - `env:` in render.yaml is deprecated; use `runtime:`.
  - Community reports say Render **appends** to a client-sent `X-Forwarded-For`, so never trust its first entry.
  - The free plan sleeps after inactivity.
- **Starlette 1.x** requires `TemplateResponse(request, "page.html")`. The old `(name, {"request": …})` form crashes.
- **Tests use `httpx2`** (successor to httpx, same author), which Starlette's TestClient now prefers.
- **pip-compile** needs `--allow-unsafe` for the dev lock.
- **Windows dev machine:**
  - PowerShell may block `Activate.ps1` (see section 6).
  - Python printing special characters to the console can fail (cp1252), so set `PYTHONIOENCODING=utf-8`.
  - Line endings are normalised to LF by `.gitattributes`.
- **Browser dates:** use the **local** date, not `toISOString()`, which is UTC and gives yesterday's date for Indian users until 5:30 am.
- **Behind Render's proxy,** `url_for()` builds `http://` addresses, which browsers block as mixed content on the HTTPS site. Templates use relative `/static/...` paths instead (tested).
- **Jinja escapes apostrophes** as `&#39;`, so tests that look for page text containing one must `html.unescape()` first.
- **CSS:**
  - Author rules like `.message { display:flex }` override the `hidden` attribute, so styles.css forces `[hidden] { display:none !important }`.
  - Padding plus `width: 100%` overflows unless `box-sizing: border-box` is set.
- **PDF text:** only text-based PDFs work. The text extracted from a PDF also counts against `MAX_TEXT_CHARS`.

---

## 12. Suggested next step

Finish the open L14 checks in [section 9.1](#91-necessary-before-going-live-in-this-order). Then, before promoting the site widely, add the contact email and get the lawyer review. After that, choose from [section 9.2](#92-optional-soon-after-launch).
