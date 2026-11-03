from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Any, Optional
from ollama_client import OllamaClient
import traceback
import logging
import os
import PyPDF2
import io

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get environment variables
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
OLLAMA_API_BASE = os.getenv("OLLAMA_API_BASE", "http://localhost:11434")

app = FastAPI(title="LegalEdge AI")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Set up templates
templates = Jinja2Templates(directory="templates")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Ollama client
try:
    logger.info("Initializing Ollama client...")
    ollama_client = OllamaClient(base_url=OLLAMA_API_BASE)
    logger.info("Ollama client initialized successfully")
except Exception as e:
    logger.error(f"Error initializing Ollama client: {str(e)}")
    logger.error("Please make sure Ollama is running and the model is available.")
    raise

# Request models
class Question(BaseModel):
    question: str

class DocumentRequest(BaseModel):
    prompt: str

class ContractRequest(BaseModel):
    contract_text: str

class ComplianceRequest(BaseModel):
    legal_text: str

class SimplificationRequest(BaseModel):
    legal_text: str

def handle_ollama_error(e: Exception) -> HTTPException:
    """Convert Ollama errors to appropriate HTTP exceptions."""
    error_msg = str(e)
    logger.error(f"Handling Ollama error: {error_msg}")
    
    if "Could not connect to Ollama server" in error_msg:
        return HTTPException(
            status_code=503,
            detail="Ollama server is not running. Please start Ollama and try again."
        )
    elif "Model" in error_msg and "is not available" in error_msg:
        return HTTPException(
            status_code=503,
            detail="Required LLM model is not available. Please run 'ollama pull phi' and try again."
        )
    else:
        logger.error(f"Unexpected error: {traceback.format_exc()}")
        return HTTPException(
            status_code=500,
            detail=f"Error processing request: {error_msg}"
        )

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/legal-assistant", response_class=HTMLResponse)
async def legal_assistant_page(request: Request):
    return templates.TemplateResponse("legal-assistant.html", {"request": request})

@app.get("/document-generator", response_class=HTMLResponse)
async def document_generator_page(request: Request):
    return templates.TemplateResponse("document-generator.html", {"request": request})

@app.get("/contract-analyzer", response_class=HTMLResponse)
async def contract_analyzer_page(request: Request):
    return templates.TemplateResponse("contract-analyzer.html", {"request": request})

@app.get("/compliance-checker", response_class=HTMLResponse)
async def compliance_checker_page(request: Request):
    return templates.TemplateResponse("compliance-checker.html", {"request": request})

@app.get("/document-simplifier", response_class=HTMLResponse)
async def document_simplifier_page(request: Request):
    return templates.TemplateResponse("document-simplifier.html", {"request": request})

@app.post("/legal-assistant")
async def legal_assistant(question: Question):
    try:
        logger.info(f"Processing legal assistant request: {question.question}")
        response = ollama_client.legal_assistant(question.question)
        return {"response": response}
    except Exception as e:
        logger.error(f"Error in legal_assistant endpoint: {str(e)}")
        raise handle_ollama_error(e)

@app.post("/generate-document")
async def generate_document(request: DocumentRequest):
    try:
        logger.info(f"Received document generation request with prompt: {request.prompt}")
        response = ollama_client.generate_document(request.prompt)
        logger.info(f"Successfully generated document of length: {len(response)}")
        return {"response": response}
    except Exception as e:
        logger.error(f"Error in generate_document endpoint: {str(e)}")
        logger.error(f"Stack trace: {traceback.format_exc()}")
        raise handle_ollama_error(e)

@app.post("/analyze-contract")
async def analyze_contract(request: ContractRequest):
    try:
        logger.info("Processing contract analysis request")
        response = ollama_client.analyze_contract(request.contract_text)
        return response
    except Exception as e:
        logger.error(f"Error in analyze_contract endpoint: {str(e)}")
        raise handle_ollama_error(e)

@app.post("/check-compliance")
async def check_compliance(request: ComplianceRequest):
    try:
        logger.info("Processing compliance check request")
        response = ollama_client.check_compliance(request.legal_text)
        return response
    except Exception as e:
        logger.error(f"Error in check_compliance endpoint: {str(e)}")
        raise handle_ollama_error(e)

@app.post("/simplify-document")
async def simplify_document(request: SimplificationRequest):
    try:
        logger.info("Processing document simplification request")
        if not request.legal_text or not request.legal_text.strip():
            raise HTTPException(
                status_code=400,
                detail="Legal text cannot be empty"
            )
            
        response = ollama_client.simplify_document(request.legal_text)
        if not response:
            raise HTTPException(
                status_code=500,
                detail="Failed to generate simplified text"
            )
            
        return {"response": response}
    except ValueError as e:
        logger.error(f"Validation error in simplify_document endpoint: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error in simplify_document endpoint: {str(e)}")
        logger.error(f"Stack trace: {traceback.format_exc()}")
        raise handle_ollama_error(e)

@app.post("/simplify-document-pdf")
async def simplify_document_pdf(file: UploadFile = File(...)):
    try:
        logger.info(f"Received PDF file for simplification: {file.filename}")
        
        # Read the PDF file
        contents = await file.read()
        pdf_file = io.BytesIO(contents)
        
        # Extract text from PDF
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text()
        
        if not text.strip():
            raise HTTPException(
                status_code=400,
                detail="Could not extract text from the PDF file"
            )
        
        # Simplify the extracted text
        logger.info("Simplifying extracted text from PDF")
        response = ollama_client.simplify_document(text)
        return {"response": response}
        
    except Exception as e:
        logger.error(f"Error processing PDF: {str(e)}")
        logger.error(f"Stack trace: {traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing PDF: {str(e)}"
        )

@app.post("/analyze-contract-pdf")
async def analyze_contract_pdf(file: UploadFile = File(...)):
    try:
        logger.info(f"Received PDF file: {file.filename}")
        
        # Read the PDF file
        contents = await file.read()
        pdf_file = io.BytesIO(contents)
        
        # Extract text from PDF
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text()
        
        if not text.strip():
            raise HTTPException(
                status_code=400,
                detail="Could not extract text from the PDF file"
            )
        
        # Analyze the extracted text
        logger.info("Analyzing extracted text from PDF")
        response = ollama_client.analyze_contract(text)
        return response
        
    except Exception as e:
        logger.error(f"Error processing PDF: {str(e)}")
        logger.error(f"Stack trace: {traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing PDF: {str(e)}"
        )

@app.post("/check-compliance-pdf")
async def check_compliance_pdf(file: UploadFile = File(...)):
    try:
        logger.info(f"Received PDF file for compliance check: {file.filename}")
        
        # Read the PDF file
        contents = await file.read()
        pdf_file = io.BytesIO(contents)
        
        # Extract text from PDF
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text()
        
        if not text.strip():
            raise HTTPException(
                status_code=400,
                detail="Could not extract text from the PDF file"
            )
        
        # Check compliance of the extracted text
        logger.info("Checking compliance of extracted text from PDF")
        response = ollama_client.check_compliance(text)
        return response
        
    except Exception as e:
        logger.error(f"Error processing PDF: {str(e)}")
        logger.error(f"Stack trace: {traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing PDF: {str(e)}"
        )

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port) 