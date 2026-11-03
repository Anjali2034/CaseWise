import requests
import json
from typing import Generator, Dict, Any
import time
import logging
import traceback
import re

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LLMResponseFormatError(Exception):
    """The model replied, but not in the structure we asked for (e.g. invalid JSON)."""


class OllamaClient:
    def __init__(self, base_url="http://localhost:11434"):
        self.base_url = base_url
        self.model = "llama2"
        self.timeout = 120  # Increased timeout to 120 seconds
        self.max_retries = 3
        self.retry_delay = 2  # Delay between retries in seconds

    def check_health(self) -> Dict[str, Any]:
        """Report whether the Ollama server is reachable and the model is installed.

        Never raises, so it is safe to call at startup and from /health.
        """
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            installed = [m.get("name", "") for m in response.json().get("models", [])]
            model_available = any(
                name == self.model or name.split(":")[0] == self.model for name in installed
            )
            return {"ollama_reachable": True, "model": self.model, "model_available": model_available}
        except (requests.exceptions.RequestException, ValueError) as e:
            logger.warning(f"Ollama server at {self.base_url} is not reachable: {e}")
            return {"ollama_reachable": False, "model": self.model, "model_available": False}

    def _generate_stream(self, prompt, system_prompt=None):
        """Generate a stream of text from the model."""
        max_retries = self.max_retries
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                logger.info(f"Making request to Ollama server (attempt {retry_count + 1}/{max_retries})")
                response = requests.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "system": system_prompt,
                        "stream": True
                    },
                    timeout=120  # Increased timeout to 120 seconds
                )
                response.raise_for_status()
                
                for line in response.iter_lines():
                    if line:
                        try:
                            json_response = json.loads(line)
                            if 'response' in json_response:
                                yield json_response['response']
                            if json_response.get('done', False):
                                break
                        except json.JSONDecodeError as e:
                            logger.error(f"Error decoding JSON response: {str(e)}")
                            logger.error(f"Raw response: {line}")
                            continue
                
                return  # Successfully completed
                
            except requests.exceptions.Timeout as e:
                retry_count += 1
                logger.error(f"Attempt {retry_count} failed: {str(e)}")
                if retry_count < max_retries:
                    logger.info(f"Retrying in 2 seconds...")
                    time.sleep(2)
                else:
                    raise Exception(f"Failed to generate response after {max_retries} attempts: {str(e)}")
                    
            except requests.exceptions.ConnectionError as e:
                retry_count += 1
                logger.error(f"Attempt {retry_count} failed: {str(e)}")
                if retry_count < max_retries:
                    logger.info(f"Retrying in 2 seconds...")
                    time.sleep(2)
                else:
                    # main.handle_ollama_error matches on this text to return a 503
                    raise Exception(
                        "Could not connect to Ollama server. Please make sure it's running by executing 'ollama serve' in a terminal."
                    )

            except requests.exceptions.RequestException as e:
                retry_count += 1
                logger.error(f"Attempt {retry_count} failed: {str(e)}")
                if retry_count < max_retries:
                    logger.info(f"Retrying in 2 seconds...")
                    time.sleep(2)
                else:
                    raise Exception(f"Failed to generate response after {max_retries} attempts: {str(e)}")
                    
            except Exception as e:
                logger.error(f"Unexpected error: {str(e)}")
                logger.error(f"Stack trace: {traceback.format_exc()}")
                raise

    def generate_document(self, prompt: str) -> str:
        """Generate a legal document based on the prompt."""
        logger.info(f"Starting document generation with prompt: {prompt}")
        system_prompt = """You are a legal document generator. Generate a professional legal document based on the user's requirements. 
        Include all necessary clauses and sections. Format the output in a clear, structured manner."""
        
        full_response = ""
        try:
            for chunk in self._generate_stream(prompt, system_prompt):
                full_response += chunk
                logger.info(f"Received chunk: {chunk[:100]}...")
            
            if not full_response:
                logger.error("No response generated from Ollama")
                raise Exception("No response generated from Ollama")
                
            logger.info(f"Successfully generated document of length: {len(full_response)}")
            return full_response
        except Exception as e:
            logger.error(f"Error in generate_document: {str(e)}")
            logger.error(f"Stack trace: {traceback.format_exc()}")
            raise

    def analyze_contract(self, contract_text: str) -> Dict[str, Any]:
        """Analyze a contract and extract key information."""
        system_prompt = """You are a legal contract analyzer. Analyze the provided contract and provide a response in the following JSON format:
        {
            "key_clauses": [
                {
                    "name": "clause name",
                    "purpose": "clause purpose"
                }
            ],
            "potential_risks": [
                "risk description"
            ],
            "summary": "brief summary of the contract's main points"
        }
        Ensure your response is valid JSON and follows this exact structure."""
        
        prompt = f"Analyze this contract and provide the analysis in the specified JSON format:\n\n{contract_text}"
        # Connection/model errors propagate to the endpoint (-> 503/500).
        full_response = ""
        for chunk in self._generate_stream(prompt, system_prompt):
            full_response += chunk

        # Try to parse the response as JSON
        try:
            return json.loads(full_response)
        except json.JSONDecodeError:
            # If parsing fails, try to extract JSON from the response
            # Look for content between curly braces
            json_match = re.search(r'\{.*\}', full_response, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass

        logger.error(f"analyze_contract: model output was not valid JSON ({len(full_response)} chars)")
        raise LLMResponseFormatError("Contract analysis returned an unexpected format")

    def legal_assistant(self, question: str) -> str:
        """Provide legal advice or answer legal questions."""
        logger.info("Processing legal assistant request")
        system_prompt = """You are a legal assistant. Provide clear, accurate, and helpful responses to legal questions. 
        Keep responses concise and practical. Focus on the most relevant legal principles and implications."""
        
        full_response = ""
        try:
            for chunk in self._generate_stream(question, system_prompt):
                full_response += chunk
            logger.info("Successfully generated legal assistant response")
            return full_response
        except Exception as e:
            logger.error(f"Error in legal_assistant: {str(e)}")
            raise

    def check_compliance(self, legal_text: str) -> Dict[str, Any]:
        """Check legal text for compliance and required clauses."""
        system_prompt = """You are a legal compliance checker. Analyze the provided legal text and provide a response in the following JSON format:
        {
            "missing_clauses": [
                "list of missing required clauses"
            ],
            "compliance_issues": [
                "list of potential compliance issues"
            ],
            "recommendations": [
                "list of recommended additions or changes"
            ],
            "summary": "brief summary of compliance status"
        }
        IMPORTANT: Your response MUST be a valid JSON object. Do not include any text before or after the JSON object. Do not use markdown formatting or bullet points."""
        
        prompt = f"Check compliance for this legal text and provide the analysis in the specified JSON format:\n\n{legal_text}"
        # Connection/model errors propagate to the endpoint (-> 503/500).
        full_response = ""
        for chunk in self._generate_stream(prompt, system_prompt):
            full_response += chunk

        # Try to parse the response as JSON
        try:
            return json.loads(full_response)
        except json.JSONDecodeError:
            # If parsing fails, try to extract JSON from the response
            json_match = re.search(r'\{.*\}', full_response, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass

        # If all parsing attempts fail, parse the text response into structured data
        sections = full_response.split('\n\n')
        result = {
            "missing_clauses": [],
            "compliance_issues": [],
            "recommendations": [],
            "summary": ""
        }

        current_section = None
        for section in sections:
            if "Missing Clauses:" in section:
                current_section = "missing_clauses"
                continue
            elif "Potential Compliance Issues:" in section:
                current_section = "compliance_issues"
                continue
            elif "Recommendations:" in section:
                current_section = "recommendations"
                continue
            elif "Summary:" in section:
                current_section = "summary"
                continue

            if current_section == "summary":
                result["summary"] = section.strip()
            elif current_section and section.strip():
                # Remove bullet points and clean up the text
                cleaned_text = section.replace('*', '').strip()
                if cleaned_text:
                    result[current_section].append(cleaned_text)

        if not any(result.values()):
            logger.error(f"check_compliance: could not parse model output ({len(full_response)} chars)")
            raise LLMResponseFormatError("Compliance check returned an unexpected format")
        return result

    def simplify_document(self, legal_text: str) -> str:
        """Convert complex legal text into plain English."""
        try:
            logger.info("Starting document simplification")
            if not legal_text or not legal_text.strip():
                raise ValueError("Legal text cannot be empty")
                
            system_prompt = """You are a legal document simplifier. Convert complex legal text into clear, plain English while maintaining the original meaning and intent. 
            Use simple language and structure the output in an easy-to-understand format."""
            
            prompt = f"Simplify this legal text:\n\n{legal_text}"
            logger.info(f"Generated prompt for simplification: {prompt[:100]}...")
            
            full_response = ""
            for chunk in self._generate_stream(prompt, system_prompt):
                full_response += chunk
                    
            if not full_response:
                raise Exception("No response generated from Ollama")
                
            logger.info("Successfully simplified document")
            return full_response
            
        except Exception as e:
            logger.error(f"Error in simplify_document: {str(e)}")
            logger.error(f"Stack trace: {traceback.format_exc()}")
            raise 