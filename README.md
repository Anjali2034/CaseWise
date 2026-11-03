# CaseWise

CaseWise is an AI legal document assistant. It helps you understand, draft and review legal documents in plain language. Run it with a language model on your own computer (Ollama), or with Groq's hosted API, which the live site uses.

> **Not legal advice.** CaseWise gives general legal information. AI can make mistakes, so have a qualified lawyer review anything important.

## What it does

| Tool | What you give it | What you get back |
|---|---|---|
| **Legal Assistant** | A legal question | A plain-language explanation |
| **Document Generator** | A description of the document you need | A draft document, with `[PLACEHOLDERS]` for details you didn't provide |
| **Contract Analyzer** | Contract text or a PDF | Key clauses, potential risks and a summary |
| **Compliance Checker** | Legal text or a PDF | Missing clauses, compliance issues and recommendations |
| **Document Simplifier** | Legal text or a PDF | The same text in plain English |

## Privacy

- **Your choice of AI:** locally, CaseWise talks to [Ollama](https://ollama.com) on your own computer, so documents never leave it. The live site uses [Groq](https://groq.com), whose [terms](https://console.groq.com/docs/legal/services-agreement) say it doesn't train on API inputs or outputs and [doesn't keep them by default](https://console.groq.com/docs/your-data).
- **Nothing is stored:** there is no database. Uploaded documents are processed in memory.
- **Nothing sensitive in the logs:** server logs record sizes ("212 chars"), never the text of questions, documents or file names.

## Requirements

- **Python 3.13** (the version used for development, tests and deployment)
- For the AI features, **either** [Ollama](https://ollama.com) with a model installed (local, private) **or** a free [Groq API key](https://console.groq.com/keys). The website still loads without either; the AI tools reply that the AI service is unavailable.

## Getting started

### 1. Choose an AI provider

**Option A: Ollama (local, private, the default).** Download Ollama from [ollama.com](https://ollama.com), then pull a model:

```bash
ollama pull llama2
```

`llama2` is the default. Newer models such as `llama3.1` handle much longer documents (a larger context window), so they're recommended for contracts: run `ollama pull llama3.1` and set `OLLAMA_MODEL=llama3.1` (see [Configuration](#configuration)).

**Option B: Groq (hosted, what the live site uses).** Create a free API key at [console.groq.com/keys](https://console.groq.com/keys), then put these in a `.env` file (see [Configuration](#configuration)):

```bash
LLM_PROVIDER=groq
GROQ_API_KEY=your-key-here
```

### 2. Install CaseWise

```bash
git clone https://github.com/Anjali2034/Casewise-AI.git
cd Casewise-AI
python -m venv venv
```

Activate the virtual environment:

- Windows: `venv\Scripts\activate`
- macOS / Linux: `source venv/bin/activate`

Then install the dependencies:

```bash
pip install -r requirements.txt
```

### 3. Run it

```bash
uvicorn main:app --reload
```

Open <http://localhost:8000>. To check that CaseWise can reach the AI provider, open <http://localhost:8000/health>:

```json
{"status": "ok", "provider": "ollama", "model": "llama2", "reachable": true, "model_available": true}
```

`"status": "degraded"` means the AI service isn't reachable (Ollama isn't running, or the Groq key is missing or wrong) or the model isn't available. The other fields tell you which.

## Configuration

Settings are read from environment variables, or from a `.env` file in the project folder. Copy [.env.example](.env.example) to `.env` and edit it. `.env` is git-ignored, so it is never committed.

| Setting | Default | What it does |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama` (local) or `groq` (hosted) |
| `OLLAMA_API_BASE` | `http://localhost:11434` | Where the Ollama server is running |
| `OLLAMA_MODEL` | `llama2` | Which Ollama model to use. It must be installed (`ollama pull <name>`) |
| `GROQ_API_KEY` | *(empty)* | Your Groq API key. Keep it secret: put it in `.env` or your host's secret settings, never in the code |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Which [Groq model](https://console.groq.com/docs/models) to use. gpt-oss-120b gave the most accurate legal answers in testing |
| `AI_REQUESTS_PER_MINUTE` | `5` | Per-visitor limit on the AI tools, per minute (`0` = off) |
| `AI_REQUESTS_PER_DAY` | `50` | Per-visitor limit on the AI tools, per day (`0` = off) |
| `TRUST_PROXY_HEADERS` | `false` | Set to `true` behind a trusted proxy (like Render), so rate limits use the visitor's IP instead of the proxy's |
| `MAX_TEXT_CHARS` | `20000` with Groq, `50000` with Ollama | Maximum characters per request (pasted text, or text extracted from a PDF). Groq's free plan allows 8,000 tokens per minute, and a request has to fit in that |
| `MAX_PDF_MB` | `10` | Maximum PDF upload size in megabytes |
| `ALLOWED_ORIGINS` | *(empty)* | Other websites allowed to call the API from a browser (CORS), comma-separated. Leave empty unless you host a separate frontend |
| `PORT` | `8000` | Port used by `python main.py` |

## Running the tests

```bash
pip install -r requirements-dev.txt
pytest
```

The tests don't need Ollama: they replace the model with a fake one. They cover every page and API endpoint, error handling, retries, input limits, privacy (no document text in logs), XSS escaping, accessibility checks and branding.

## API

The pages call these endpoints, and you can call them directly too. Interactive docs are at <http://localhost:8000/docs>.

| Method | Path | Body | Returns |
|---|---|---|---|
| `POST` | `/legal-assistant` | `{"question": "..."}` | `{"response": "..."}` |
| `POST` | `/generate-document` | `{"prompt": "..."}` | `{"response": "..."}` |
| `POST` | `/analyze-contract` | `{"contract_text": "..."}` | `{"key_clauses": [...], "potential_risks": [...], "summary": "..."}` |
| `POST` | `/check-compliance` | `{"legal_text": "..."}` | `{"missing_clauses": [...], "compliance_issues": [...], "recommendations": [...], "summary": "..."}` |
| `POST` | `/simplify-document` | `{"legal_text": "..."}` | `{"response": "..."}` |
| `POST` | `/analyze-contract-pdf`, `/check-compliance-pdf`, `/simplify-document-pdf` | A PDF as `file` (multipart form) | Same as the text version |
| `GET` | `/health` | — | Whether Ollama is reachable and the model is installed |

Errors return `{"detail": "a readable message"}` with a meaningful status code:

| Status | Meaning |
|---|---|
| `400` | Empty text, an invalid request, or a file that isn't a readable PDF |
| `413` | Text longer than `MAX_TEXT_CHARS`, a PDF larger than `MAX_PDF_MB`, or a document too long for the AI model |
| `429` | Too many requests: the visitor's rate limit, or the AI provider is busy. A `Retry-After` header says how many seconds to wait |
| `502` | The AI returned an empty or badly formatted answer |
| `503` | The AI service isn't reachable or isn't configured, or the model isn't available |
| `504` | The AI took too long to answer |
| `500` | Unexpected error. The message includes an error ID that also appears in the server log |

## Project structure

```
main.py                 Web app: pages, API endpoints, input limits, error handling
llm_client.py           Talks to the AI (Ollama or Groq): streaming, retries, parsing the model's answers
rate_limit.py           Per-visitor limits on the AI tools
prompts.py              The instructions sent to the AI for each tool, with shared safety rules
templates/              HTML pages (Jinja2)
  base.html             Shared layout, navbar and JavaScript helpers
  partials/             Reusable pieces: "not legal advice" notice, PDF upload box
static/css/styles.css   All shared styles
tests/                  pytest test suite
notebooks/              Early experiments (not used by the app)
requirements.in         Packages CaseWise uses directly (edit this one)
requirements.txt        Lock file generated from requirements.in (every package pinned, with hashes)
render.yaml, Procfile   Deployment configuration
```

## Updating dependencies

`requirements.txt` and `requirements-dev.txt` are **generated** lock files: they pin every package, including indirect ones, to an exact version with a security hash. Don't edit them by hand. Instead, change `requirements.in` (or `requirements-dev.in` for test tools), then regenerate:

```bash
pip install -r requirements-dev.txt
pip-compile --generate-hashes requirements.in
pip-compile --generate-hashes --allow-unsafe requirements-dev.in
pytest
```

## Deployment

The live site runs on [Render](https://render.com)'s free plan, using Groq for the AI. [render.yaml](render.yaml) describes the whole service.

1. **Get a Groq API key** at [console.groq.com/keys](https://console.groq.com/keys).
2. **Turn on Zero Data Retention** in Groq's [Data Controls settings](https://console.groq.com/settings/data-controls), so Groq doesn't keep request logs even for troubleshooting.
3. In Render, choose **New → Blueprint** and connect this GitHub repository. Render reads `render.yaml` and asks for `GROQ_API_KEY`: paste the key there. It's stored as a secret, never in the code.
4. When the deploy finishes, open `https://<your-service>.onrender.com/health` and check for `"status": "ok"`.

Groq's free plan allows **1,000 requests a day and 8,000 tokens a minute**, shared by all visitors. A typical request uses about 400-900 tokens. When the quota runs out, users see "CaseWise is very busy right now" and can retry a minute later.

On the free plan, the service sleeps after about 15 minutes without visitors, and the next visit takes 30-60 seconds to wake it up. Rate limits are kept in memory, so they reset when it restarts.
