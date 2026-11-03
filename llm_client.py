import requests
import json
from typing import Dict, Any
import time
import logging
import re

import prompts

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Base class for errors talking to the language model."""


class OllamaUnavailableError(LLMError):
    """The Ollama server could not be reached."""


class ModelNotFoundError(LLMError):
    """The configured model is not installed in Ollama."""


class LLMTimeoutError(LLMError):
    """The model did not answer within the read timeout."""


class LLMResponseFormatError(LLMError):
    """The model replied, but not in the structure we asked for (e.g. invalid JSON)."""


class OllamaClient:
    def __init__(self, base_url="http://localhost:11434", model="llama2"):
        self.base_url = base_url
        self.model = model
        self.connect_timeout = 5   # seconds to establish the connection
        self.read_timeout = 120    # max seconds to wait between streamed chunks
        self.max_attempts = 2      # 1 retry, transient errors only
        self.retry_delay = 1       # seconds between attempts

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
        """Stream text chunks from the model.

        Retry policy: only transient failures (connection errors, 5xx) are retried,
        only once, and only before any text has been yielded, so partial output is
        never duplicated. Timeouts and 4xx errors fail immediately.
        """
        payload = {"model": self.model, "prompt": prompt, "system": system_prompt, "stream": True}
        attempt = 0
        yielded_any = False

        while True:
            attempt += 1
            try:
                logger.info(f"Making request to Ollama server (attempt {attempt}/{self.max_attempts})")
                with requests.post(
                    f"{self.base_url}/api/generate",
                    json=payload,
                    stream=True,  # read the reply as it is generated
                    timeout=(self.connect_timeout, self.read_timeout),
                ) as response:
                    if response.status_code == 404:
                        raise ModelNotFoundError(
                            f"Model '{self.model}' is not available. Run 'ollama pull {self.model}'."
                        )
                    response.raise_for_status()

                    for line in response.iter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            logger.warning("Skipping a malformed line from the Ollama stream")
                            continue
                        if data.get("error"):
                            raise LLMError(f"Ollama error: {data['error']}")
                        if data.get("response"):
                            yielded_any = True
                            yield data["response"]
                        if data.get("done"):
                            break
                return

            except requests.exceptions.Timeout as e:
                logger.error(f"Ollama request timed out: {e}")
                raise LLMTimeoutError("The AI model took too long to respond.") from e

            except (requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as e:
                is_5xx = isinstance(e, requests.exceptions.HTTPError) and e.response is not None                     and e.response.status_code >= 500
                transient = isinstance(e, requests.exceptions.ConnectionError) or is_5xx
                if transient and not yielded_any and attempt < self.max_attempts:
                    logger.warning(f"Attempt {attempt} failed ({e}); retrying in {self.retry_delay}s")
                    time.sleep(self.retry_delay)
                    continue
                logger.error(f"Ollama request failed: {e}")
                if isinstance(e, requests.exceptions.ConnectionError) and not yielded_any:
                    raise OllamaUnavailableError(
                        "Could not connect to Ollama server. Please make sure it's running by executing 'ollama serve' in a terminal."
                    ) from e
                raise LLMError(f"Ollama request failed: {e}") from e

            except requests.exceptions.RequestException as e:
                # e.g. the stream was cut off mid-response (ChunkedEncodingError).
                # Never retried: some text may already have been yielded.
                logger.error(f"Ollama stream failed: {e}")
                raise LLMError(f"Ollama stream failed: {e}") from e

    def _complete(self, prompt: str, system_prompt: str) -> str:
        """Run the model and return its whole answer as one string ("" if it said nothing).

        Errors propagate as LLMError subclasses; main.run_tool turns them, and empty
        answers, into the right HTTP response.
        """
        return "".join(self._generate_stream(prompt, system_prompt))

    def generate_document(self, prompt: str) -> str:
        """Generate a legal document based on the prompt."""
        return self._complete(prompt, prompts.DOCUMENT_GENERATOR)

    def analyze_contract(self, contract_text: str) -> Dict[str, Any]:
        """Analyze a contract and extract key information."""
        system_prompt = prompts.CONTRACT_ANALYZER
        
        prompt = f"Analyze this contract and provide the analysis in the specified JSON format:\n\n{contract_text}"
        full_response = self._complete(prompt, system_prompt)

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
        """Answer a legal question with general information (not legal advice)."""
        return self._complete(question, prompts.LEGAL_ASSISTANT)

    def check_compliance(self, legal_text: str) -> Dict[str, Any]:
        """Check legal text for compliance and required clauses."""
        system_prompt = prompts.COMPLIANCE_CHECKER
        
        prompt = f"Check compliance for this legal text and provide the analysis in the specified JSON format:\n\n{legal_text}"
        full_response = self._complete(prompt, system_prompt)

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
        return self._complete(f"Simplify this legal text:\n\n{legal_text}", prompts.DOCUMENT_SIMPLIFIER)
