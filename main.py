from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints
from typing import Annotated
from llm_client import (
    DocumentTooLongError,
    GroqClient,
    LLMError,
    LLMRateLimitedError,
    LLMResponseFormatError,
    LLMTimeoutError,
    ModelNotFoundError,
    OllamaClient,
    ServiceUnavailableError,
)
from rate_limit import RateLimiter, client_key
from dotenv import load_dotenv
import traceback
import logging
import uuid
import os
from pypdf import PdfReader
import io

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load settings from a local .env file if present (see .env.example)
load_dotenv()

def parse_origins(raw: str) -> list:
    """'https://a.com/, http://localhost:3000' -> ['https://a.com', 'http://localhost:3000']"""
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


def env_flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# Get environment variables (all documented in .env.example)
ALLOWED_ORIGINS = parse_origins(os.getenv("ALLOWED_ORIGINS", ""))
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
OLLAMA_API_BASE = os.getenv("OLLAMA_API_BASE", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama2")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
AI_REQUESTS_PER_MINUTE = int(os.getenv("AI_REQUESTS_PER_MINUTE", "5"))
AI_REQUESTS_PER_DAY = int(os.getenv("AI_REQUESTS_PER_DAY", "50"))
TRUST_PROXY_HEADERS = env_flag("TRUST_PROXY_HEADERS")

app = FastAPI(title="CaseWise")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Set up templates
templates = Jinja2Templates(directory="templates")

def cors_settings(origins: list) -> dict:
    """CORS options for the given allowed origins.

    The app's own pages are same-origin and need no CORS, so with no origins
    configured the middleware is not installed at all. Credentials (cookies)
    are only ever allowed for an explicit list of origins, never with "*".
    """
    return {
        "allow_origins": origins,
        "allow_credentials": "*" not in origins,
        "allow_methods": ["GET", "POST"],
        "allow_headers": ["Content-Type"],
    }


# Enable CORS only when other origins are explicitly allowed (see .env.example)
if ALLOWED_ORIGINS:
    app.add_middleware(CORSMiddleware, **cors_settings(ALLOWED_ORIGINS))

def make_llm_client():
    """The AI provider: local Ollama (default, private) or hosted Groq (the live site)."""
    if LLM_PROVIDER == "ollama":
        return OllamaClient(base_url=OLLAMA_API_BASE, model=OLLAMA_MODEL)
    if LLM_PROVIDER == "groq":
        return GroqClient(api_key=GROQ_API_KEY, model=GROQ_MODEL)
    raise ValueError(f"Unknown LLM_PROVIDER '{LLM_PROVIDER}'. Use 'ollama' or 'groq'.")


# The app starts even if the AI service is down; AI requests return 503 until it's reachable. See /health.
llm = make_llm_client()
_startup_health = llm.check_health()
if not _startup_health["reachable"]:
    if llm.provider == "ollama":
        logger.warning(f"Ollama is not reachable at {OLLAMA_API_BASE}. AI features will fail until it is running.")
    else:
        logger.warning("The Groq API is not reachable, or GROQ_API_KEY is missing or invalid.")
elif not _startup_health["model_available"]:
    hint = f" Run: ollama pull {llm.model}" if llm.provider == "ollama" else ""
    logger.warning(f"Model '{llm.model}' is not available.{hint}")
else:
    logger.info(f"AI ready: {llm.provider} (model: {llm.model})")

# Per-visitor limits on the AI endpoints, so nobody can use up the shared AI quota
rate_limiter = RateLimiter(per_minute=AI_REQUESTS_PER_MINUTE, per_day=AI_REQUESTS_PER_DAY)


def ai_rate_limit(request: Request) -> None:
    """FastAPI dependency on every AI endpoint: 429 once a visitor is over the limit."""
    key = client_key(request.client.host if request.client else None, request.headers, TRUST_PROXY_HEADERS)
    blocked = rate_limiter.check(key)
    if blocked:
        retry_after, window = blocked
        if window == "day":
            detail = f"You've reached today's limit of {rate_limiter.per_day} requests. Please try again tomorrow."
        else:
            detail = f"You're sending requests too quickly. Please wait {retry_after} seconds and try again."
        logger.info(f"Rate limit reached ({window})")
        raise HTTPException(status_code=429, detail=detail, headers={"Retry-After": str(retry_after)})

# Input limits (configurable, see .env.example).
# Groq's free plan accepts at most 8,000 tokens per minute, and one request (document
# + instructions + answer) has to fit in that, so its default text limit is lower.
DEFAULT_MAX_TEXT_CHARS = {"groq": 20000, "ollama": 50000}
MAX_TEXT_CHARS = int(os.getenv("MAX_TEXT_CHARS") or DEFAULT_MAX_TEXT_CHARS.get(LLM_PROVIDER, 50000))
MAX_PDF_BYTES = int(float(os.getenv("MAX_PDF_MB", "10")) * 1024 * 1024)

# Request models. Every text field is stripped, must be non-empty and has a max length.
InputText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TEXT_CHARS)]

class Question(BaseModel):
    question: InputText

class DocumentRequest(BaseModel):
    prompt: InputText

class ContractRequest(BaseModel):
    contract_text: InputText

class ComplianceRequest(BaseModel):
    legal_text: InputText

class SimplificationRequest(BaseModel):
    legal_text: InputText


def text_too_long_error() -> HTTPException:
    return HTTPException(
        status_code=413,
        detail=f"The text is too long. The maximum is {MAX_TEXT_CHARS:,} characters."
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Turn FastAPI's 422 error list into one readable message the pages can show."""
    error = exc.errors()[0]
    if error["type"] == "string_too_short":
        status, detail = 400, "The text cannot be empty."
    elif error["type"] == "string_too_long":
        too_long = text_too_long_error()
        status, detail = too_long.status_code, too_long.detail
    else:
        field = ".".join(str(part) for part in error["loc"] if part != "body")
        status, detail = 400, f"Invalid request: '{field}' {error['msg'].lower()}."
    return JSONResponse(status_code=status, content={"detail": detail})

def handle_llm_error(e: Exception) -> HTTPException:
    """Convert LLM errors to the right HTTP status, with a message that's safe to show."""
    logger.error(f"AI request failed: {type(e).__name__}: {e}")
    message = getattr(e, "user_message", None)

    if isinstance(e, LLMResponseFormatError):
        return HTTPException(status_code=502, detail="The AI returned its answer in an unexpected format. Please try again.")
    if isinstance(e, LLMRateLimitedError):
        headers = {"Retry-After": str(e.retry_after)} if e.retry_after else None
        return HTTPException(status_code=429, headers=headers,
                             detail="CaseWise is very busy right now. Please try again in a minute.")
    if isinstance(e, DocumentTooLongError):
        return HTTPException(status_code=413,
                             detail="This document is too long for the AI to read in one go. Please try a shorter section.")
    if isinstance(e, ServiceUnavailableError):
        return HTTPException(status_code=503, detail=message or "The AI service is temporarily unavailable. Please try again shortly.")
    if isinstance(e, ModelNotFoundError):
        return HTTPException(status_code=503, detail=message or "The AI model isn't available right now. Please try again later.")
    if isinstance(e, LLMTimeoutError):
        return HTTPException(status_code=504, detail="The AI model took too long to respond. Please try again with a shorter text.")
    if isinstance(e, LLMError):
        # Any other error reported by the AI service (e.g. the model crashed mid-answer)
        return HTTPException(status_code=502, detail="The AI service returned an error. Please try again.")
    # Never send raw exception text to the browser. Log it under a short
    # ID the user can quote, so the full trace can be found in the logs.
    error_id = uuid.uuid4().hex[:8]
    logger.error(f"Unexpected error [{error_id}]: {traceback.format_exc()}")
    return HTTPException(
        status_code=500,
        detail=f"Something went wrong while processing your request (error ID: {error_id}). Please try again."
    )

def extract_pdf_text(file: UploadFile) -> str:
    """Read an uploaded PDF and return its text. Raises HTTP 400/413 for bad input."""
    # Read at most one byte past the limit, so oversized files are rejected
    # without loading all of them into memory.
    data = file.file.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"The PDF is too large. The maximum size is {MAX_PDF_BYTES // (1024 * 1024)} MB."
        )
    # Every PDF starts with "%PDF-". Checking this is cheap and doesn't trust
    # the browser-supplied file name or content type.
    if not data.lstrip()[:5] == b"%PDF-":
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid PDF")
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "".join((page.extract_text() or "") for page in reader.pages)
    except Exception as e:
        logger.warning(f"Could not read uploaded PDF: {type(e).__name__}")
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid PDF")
    if not text.strip():
        raise HTTPException(
            status_code=400,
            detail="Could not extract text from the PDF file. Scanned (image-only) PDFs are not supported yet."
        )
    if len(text) > MAX_TEXT_CHARS:
        raise text_too_long_error()
    logger.info(f"Extracted {len(text)} chars from {len(reader.pages)} PDF page(s)")
    return text

@app.get("/health")
def health():
    """Liveness + dependency check. Always 200 while the web app is up."""
    llm_status = llm.check_health()
    ok = llm_status["reachable"] and llm_status["model_available"]
    return {"status": "ok" if ok else "degraded", **llm_status}

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "home.html")

@app.get("/legal-assistant", response_class=HTMLResponse)
async def legal_assistant_page(request: Request):
    return templates.TemplateResponse(request, "legal-assistant.html")

@app.get("/document-generator", response_class=HTMLResponse)
async def document_generator_page(request: Request):
    return templates.TemplateResponse(request, "document-generator.html")

@app.get("/contract-analyzer", response_class=HTMLResponse)
async def contract_analyzer_page(request: Request):
    return templates.TemplateResponse(request, "contract-analyzer.html")

@app.get("/compliance-checker", response_class=HTMLResponse)
async def compliance_checker_page(request: Request):
    return templates.TemplateResponse(request, "compliance-checker.html")

@app.get("/document-simplifier", response_class=HTMLResponse)
async def document_simplifier_page(request: Request):
    return templates.TemplateResponse(request, "document-simplifier.html")

def run_tool(name: str, task, text: str):
    """Run one AI tool on `text` and turn every failure into the right HTTP error.

    All eight API endpoints go through here, so error handling lives in one place.
    """
    try:
        result = task(text)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"{name} failed: {type(e).__name__}")
        raise handle_llm_error(e)
    if not result:
        raise HTTPException(status_code=502, detail="The AI returned an empty answer. Please try again.")
    return result


# ---------- Text endpoints ----------

@app.post("/legal-assistant", dependencies=[Depends(ai_rate_limit)])
def legal_assistant(question: Question):
    logger.info(f"Legal assistant request received ({len(question.question)} chars)")
    return {"response": run_tool("Legal assistant", llm.legal_assistant, question.question)}

@app.post("/generate-document", dependencies=[Depends(ai_rate_limit)])
def generate_document(request: DocumentRequest):
    logger.info(f"Document generation request received ({len(request.prompt)} chars)")
    return {"response": run_tool("Document generation", llm.generate_document, request.prompt)}

@app.post("/analyze-contract", dependencies=[Depends(ai_rate_limit)])
def analyze_contract(request: ContractRequest):
    logger.info("Contract analysis request received")
    return run_tool("Contract analysis", llm.analyze_contract, request.contract_text)

@app.post("/check-compliance", dependencies=[Depends(ai_rate_limit)])
def check_compliance(request: ComplianceRequest):
    logger.info("Compliance check request received")
    return run_tool("Compliance check", llm.check_compliance, request.legal_text)

@app.post("/simplify-document", dependencies=[Depends(ai_rate_limit)])
def simplify_document(request: SimplificationRequest):
    logger.info("Simplification request received")
    return {"response": run_tool("Simplification", llm.simplify_document, request.legal_text)}


# ---------- PDF endpoints: same tools, text comes from an uploaded PDF ----------

@app.post("/simplify-document-pdf", dependencies=[Depends(ai_rate_limit)])
def simplify_document_pdf(file: UploadFile = File(...)):
    logger.info("PDF received for simplification")
    return {"response": run_tool("Simplification (PDF)", llm.simplify_document, extract_pdf_text(file))}

@app.post("/analyze-contract-pdf", dependencies=[Depends(ai_rate_limit)])
def analyze_contract_pdf(file: UploadFile = File(...)):
    logger.info("PDF received for contract analysis")
    return run_tool("Contract analysis (PDF)", llm.analyze_contract, extract_pdf_text(file))

@app.post("/check-compliance-pdf", dependencies=[Depends(ai_rate_limit)])
def check_compliance_pdf(file: UploadFile = File(...)):
    logger.info("PDF received for compliance check")
    return run_tool("Compliance check (PDF)", llm.check_compliance, extract_pdf_text(file))


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port) 