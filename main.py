from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints
from typing import Annotated
from llm_client import (
    OllamaClient,
    LLMError,
    LLMResponseFormatError,
    LLMTimeoutError,
    ModelNotFoundError,
    OllamaUnavailableError,
)
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


# Get environment variables
ALLOWED_ORIGINS = parse_origins(os.getenv("ALLOWED_ORIGINS", ""))
OLLAMA_API_BASE = os.getenv("OLLAMA_API_BASE", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama2")

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

# Initialize Ollama client. The app starts even if Ollama is down;
# requests will return 503 until it is reachable. See /health.
llm = OllamaClient(base_url=OLLAMA_API_BASE, model=OLLAMA_MODEL)
_startup_health = llm.check_health()
if not _startup_health["ollama_reachable"]:
    logger.warning(f"Ollama is not reachable at {OLLAMA_API_BASE}. AI features will fail until it is running.")
elif not _startup_health["model_available"]:
    logger.warning(f"Model '{llm.model}' is not installed. Run: ollama pull {llm.model}")
else:
    logger.info(f"Ollama client initialized successfully (model: {OLLAMA_MODEL})")

# Input limits (configurable, see .env.example)
MAX_TEXT_CHARS = int(os.getenv("MAX_TEXT_CHARS", "50000"))
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
    """Convert LLM errors to appropriate HTTP exceptions."""
    error_msg = str(e)
    logger.error(f"Handling Ollama error: {error_msg}")

    if isinstance(e, LLMResponseFormatError):
        return HTTPException(
            status_code=502,
            detail="The AI returned its answer in an unexpected format. Please try again."
        )
    if isinstance(e, OllamaUnavailableError):
        return HTTPException(
            status_code=503,
            detail="Ollama server is not running. Please start Ollama and try again."
        )
    elif isinstance(e, ModelNotFoundError):
        return HTTPException(
            status_code=503,
            detail=f"Required LLM model is not available. Please run 'ollama pull {llm.model}' and try again."
        )
    elif isinstance(e, LLMTimeoutError):
        return HTTPException(
            status_code=504,
            detail="The AI model took too long to respond. Please try again with a shorter text."
        )
    elif isinstance(e, LLMError):
        # Any other error reported by Ollama (e.g. the model crashed mid-answer)
        return HTTPException(
            status_code=502,
            detail="The AI service returned an error. Please try again."
        )
    else:
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
    ok = llm_status["ollama_reachable"] and llm_status["model_available"]
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

@app.post("/legal-assistant")
def legal_assistant(question: Question):
    try:
        logger.info(f"Legal assistant request received ({len(question.question)} chars)")
        response = llm.legal_assistant(question.question)
        return {"response": response}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in legal_assistant endpoint: {str(e)}")
        raise handle_llm_error(e)

@app.post("/generate-document")
def generate_document(request: DocumentRequest):
    try:
        logger.info(f"Document generation request received ({len(request.prompt)} chars)")
        response = llm.generate_document(request.prompt)
        logger.info(f"Successfully generated document of length: {len(response)}")
        return {"response": response}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in generate_document endpoint: {str(e)}")
        logger.error(f"Stack trace: {traceback.format_exc()}")
        raise handle_llm_error(e)

@app.post("/analyze-contract")
def analyze_contract(request: ContractRequest):
    try:
        logger.info("Processing contract analysis request")
        response = llm.analyze_contract(request.contract_text)
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in analyze_contract endpoint: {str(e)}")
        raise handle_llm_error(e)

@app.post("/check-compliance")
def check_compliance(request: ComplianceRequest):
    try:
        logger.info("Processing compliance check request")
        response = llm.check_compliance(request.legal_text)
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in check_compliance endpoint: {str(e)}")
        raise handle_llm_error(e)

@app.post("/simplify-document")
def simplify_document(request: SimplificationRequest):
    try:
        logger.info("Processing document simplification request")
        response = llm.simplify_document(request.legal_text)
        if not response:
            raise HTTPException(
                status_code=500,
                detail="Failed to generate simplified text"
            )
            
        return {"response": response}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in simplify_document endpoint: {str(e)}")
        logger.error(f"Stack trace: {traceback.format_exc()}")
        raise handle_llm_error(e)

@app.post("/simplify-document-pdf")
def simplify_document_pdf(file: UploadFile = File(...)):
    try:
        logger.info("PDF received for simplification")
        
        text = extract_pdf_text(file)
        
        # Simplify the extracted text
        logger.info("Simplifying extracted text from PDF")
        response = llm.simplify_document(text)
        return {"response": response}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing PDF: {str(e)}")
        raise handle_llm_error(e)

@app.post("/analyze-contract-pdf")
def analyze_contract_pdf(file: UploadFile = File(...)):
    try:
        logger.info("PDF received for contract analysis")
        
        text = extract_pdf_text(file)
        
        # Analyze the extracted text
        logger.info("Analyzing extracted text from PDF")
        response = llm.analyze_contract(text)
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing PDF: {str(e)}")
        raise handle_llm_error(e)

@app.post("/check-compliance-pdf")
def check_compliance_pdf(file: UploadFile = File(...)):
    try:
        logger.info("PDF received for compliance check")
        
        text = extract_pdf_text(file)
        
        # Check compliance of the extracted text
        logger.info("Checking compliance of extracted text from PDF")
        response = llm.check_compliance(text)
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing PDF: {str(e)}")
        raise handle_llm_error(e)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port) 