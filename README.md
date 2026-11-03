# CaseWise

CaseWise is an AI legal document assistant. It helps you understand, draft and review legal documents in plain language, using a language model that can run entirely on your own computer.

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

- **Your own AI model:** CaseWise talks to [Ollama](https://ollama.com), which can run on your own computer, so documents don't have to go to a third-party AI service.
- **Nothing is stored:** there is no database. Uploaded documents are processed in memory.
- **Nothing sensitive in the logs:** server logs record sizes ("212 chars"), never the text of questions, documents or file names.

## Requirements

- **Python 3.13** (the version used for development, tests and deployment)
- **Ollama** with a model installed, for the AI features. The website still loads without it; the AI tools reply that Ollama isn't running.

## Getting started

### 1. Install Ollama and a model

Download Ollama from [ollama.com](https://ollama.com), then pull a model:

```bash
ollama pull llama2
```

`llama2` is the default. Newer models such as `llama3.1` handle much longer documents (a larger context window), so they're recommended for contracts: run `ollama pull llama3.1` and set `OLLAMA_MODEL=llama3.1` (see [Configuration](#configuration)).

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

Open <http://localhost:8000>. To check that CaseWise can reach Ollama, open <http://localhost:8000/health>:

```json
{"status": "ok", "ollama_reachable": true, "model": "llama2", "model_available": true}
```

`"status": "degraded"` means Ollama isn't running or the model isn't installed. The other fields tell you which.

## Configuration

Settings are read from environment variables, or from a `.env` file in the project folder. Copy [.env.example](.env.example) to `.env` and edit it. `.env` is git-ignored, so it is never committed.

| Setting | Default | What it does |
|---|---|---|
| `OLLAMA_API_BASE` | `http://localhost:11434` | Where the Ollama server is running |
| `OLLAMA_MODEL` | `llama2` | Which model to use. It must be installed (`ollama pull <name>`) |
| `MAX_TEXT_CHARS` | `50000` | Maximum characters per request (pasted text, or text extracted from a PDF) |
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
| `413` | Text longer than `MAX_TEXT_CHARS`, or a PDF larger than `MAX_PDF_MB` |
| `502` | The AI returned an empty or badly formatted answer |
| `503` | Ollama isn't running, or the model isn't installed |
| `504` | The AI took too long to answer |
| `500` | Unexpected error. The message includes an error ID that also appears in the server log |

## Project structure

```
main.py                 Web app: pages, API endpoints, input limits, error handling
llm_client.py           Talks to Ollama: streaming, retries, parsing the model's answers
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

[render.yaml](render.yaml) describes a [Render](https://render.com) web service. Render cannot run Ollama itself, so `OLLAMA_API_BASE` must point to an Ollama server you host elsewhere. Built-in support for a hosted AI provider is planned.
