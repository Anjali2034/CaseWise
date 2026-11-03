"""Talking to the language model.

LLMClient holds everything the providers share: the five CaseWise tools, the
retry policy, timeouts and a cached health check. Each provider only says how
to send a request and how to read one line of the streamed reply:

- OllamaClient: a local Ollama server (free, private: documents stay on your machine)
- GroqClient:   Groq's hosted API, used by the live site (Groq does not train on API data)
"""
import json
import logging
import re
import time
from typing import Any, Dict, Optional, Tuple

import requests

import prompts

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------- Errors ----------
# Every error carries an optional `user_message`: text that is safe to show to
# users. main.handle_llm_error turns each type into an HTTP status.

class LLMError(Exception):
    """Base class for errors talking to the language model."""

    def __init__(self, message: str = "", user_message: Optional[str] = None):
        super().__init__(message)
        self.user_message = user_message


class ServiceUnavailableError(LLMError):
    """The AI service can't be used right now (unreachable, or misconfigured)."""


class OllamaUnavailableError(ServiceUnavailableError):
    """The local Ollama server could not be reached."""


class ModelNotFoundError(LLMError):
    """The configured model doesn't exist (or isn't installed in Ollama)."""


class LLMTimeoutError(LLMError):
    """The model did not answer within the read timeout."""


class LLMRateLimitedError(LLMError):
    """The provider refused the request because its rate limit or quota was hit."""

    def __init__(self, message: str = "", retry_after: Optional[int] = None):
        super().__init__(message)
        self.retry_after = retry_after


class DocumentTooLongError(LLMError):
    """The text is longer than the model can read in one request."""


class LLMResponseFormatError(LLMError):
    """The model replied, but not in the structure we asked for (e.g. invalid JSON)."""


# ---------- Shared client ----------

class LLMClient:
    provider = "base"

    def __init__(self, model: str):
        self.model = model
        self.connect_timeout = 5      # seconds to establish the connection
        self.read_timeout = 120       # max seconds to wait between streamed chunks
        self.max_attempts = 2         # 1 retry, transient errors only
        self.retry_delay = 1          # seconds between attempts
        self.temperature = 0.2        # low: consistent, factual answers for legal text
        self.health_cache_seconds = 60
        self._health_cache: Optional[Tuple[float, Dict[str, Any]]] = None

    # -- provider-specific parts --

    def _request(self, prompt: str, system_prompt: str) -> Tuple[str, Dict[str, Any], Dict[str, str]]:
        """Return (url, json_body, headers) for a streaming generation request."""
        raise NotImplementedError

    def _parse_line(self, line: bytes) -> Tuple[Optional[str], bool]:
        """Read one streamed line. Return (text or None, done)."""
        raise NotImplementedError

    def _raise_for_status(self, response: requests.Response) -> None:
        """Raise a typed LLMError for HTTP error statuses the provider uses."""
        if response.status_code == 404:
            raise ModelNotFoundError(f"Model '{self.model}' was not found")
        if response.status_code == 429:
            raise LLMRateLimitedError("Rate limited by the AI provider",
                                      retry_after=_retry_after_seconds(response))
        if response.status_code == 413:
            raise DocumentTooLongError("Request too large for the model")
        response.raise_for_status()

    def _unavailable_error(self) -> ServiceUnavailableError:
        return ServiceUnavailableError("Could not connect to the AI service")

    def _check_health_uncached(self) -> Dict[str, Any]:
        raise NotImplementedError

    # -- shared logic --

    def check_health(self) -> Dict[str, Any]:
        """Whether the AI service is reachable and the model exists. Never raises.

        Cached for a minute, so frequent health checks (e.g. Render's) don't
        spend the provider's quota.
        """
        now = time.monotonic()
        if self._health_cache and now - self._health_cache[0] < self.health_cache_seconds:
            return self._health_cache[1]
        result = {"provider": self.provider, "model": self.model, **self._check_health_uncached()}
        self._health_cache = (now, result)
        return result

    def _generate_stream(self, prompt: str, system_prompt: Optional[str] = None):
        """Stream text chunks from the model.

        Retry policy: only transient failures (connection errors, 5xx) are retried,
        only once, and only before any text has been yielded, so partial output is
        never duplicated. Timeouts and 4xx errors fail immediately.
        """
        url, body, headers = self._request(prompt, system_prompt)
        attempt = 0
        yielded_any = False

        while True:
            attempt += 1
            try:
                logger.info(f"Calling {self.provider} (attempt {attempt}/{self.max_attempts})")
                with requests.post(url, json=body, headers=headers, stream=True,
                                   timeout=(self.connect_timeout, self.read_timeout)) as response:
                    self._raise_for_status(response)
                    for line in response.iter_lines():
                        if not line:
                            continue
                        text, done = self._parse_line(line)
                        if text:
                            yielded_any = True
                            yield text
                        if done:
                            break
                return

            except requests.exceptions.Timeout as e:
                logger.error(f"{self.provider} request timed out: {e}")
                raise LLMTimeoutError("The AI model took too long to respond.") from e

            except (requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as e:
                status = e.response.status_code if isinstance(e, requests.exceptions.HTTPError) and e.response is not None else None
                transient = isinstance(e, requests.exceptions.ConnectionError) or (status is not None and status >= 500)
                if transient and not yielded_any and attempt < self.max_attempts:
                    logger.warning(f"Attempt {attempt} failed ({type(e).__name__}); retrying in {self.retry_delay}s")
                    time.sleep(self.retry_delay)
                    continue
                logger.error(f"{self.provider} request failed: {e}")
                if isinstance(e, requests.exceptions.ConnectionError) and not yielded_any:
                    raise self._unavailable_error() from e
                raise LLMError(f"{self.provider} request failed: {e}") from e

            except requests.exceptions.RequestException as e:
                # e.g. the stream was cut off mid-response (ChunkedEncodingError).
                # Never retried: some text may already have been yielded.
                logger.error(f"{self.provider} stream failed: {e}")
                raise LLMError(f"{self.provider} stream failed: {e}") from e

    def _complete(self, prompt: str, system_prompt: str) -> str:
        """Run the model and return its whole answer as one string ("" if it said nothing).

        Errors propagate as LLMError subclasses; main.run_tool turns them, and empty
        answers, into the right HTTP response.
        """
        return "".join(self._generate_stream(prompt, system_prompt))

    # -- the five CaseWise tools --

    def legal_assistant(self, question: str) -> str:
        """Answer a legal question with general information (not legal advice)."""
        return self._complete(question, prompts.LEGAL_ASSISTANT)

    def generate_document(self, prompt: str) -> str:
        """Generate a legal document based on the prompt."""
        return self._complete(prompt, prompts.DOCUMENT_GENERATOR)

    def simplify_document(self, legal_text: str) -> str:
        """Convert complex legal text into plain English."""
        return self._complete(f"Simplify this legal text:\n\n{legal_text}", prompts.DOCUMENT_SIMPLIFIER)

    def analyze_contract(self, contract_text: str) -> Dict[str, Any]:
        """Analyze a contract and extract key information."""
        prompt = f"Analyze this contract and provide the analysis in the specified JSON format:\n\n{contract_text}"
        full_response = self._complete(prompt, prompts.CONTRACT_ANALYZER)
        parsed = _parse_json_object(full_response)
        if parsed is not None:
            return parsed
        logger.error(f"analyze_contract: model output was not valid JSON ({len(full_response)} chars)")
        raise LLMResponseFormatError("Contract analysis returned an unexpected format")

    def check_compliance(self, legal_text: str) -> Dict[str, Any]:
        """Check legal text for compliance and required clauses."""
        prompt = f"Check compliance for this legal text and provide the analysis in the specified JSON format:\n\n{legal_text}"
        full_response = self._complete(prompt, prompts.COMPLIANCE_CHECKER)
        parsed = _parse_json_object(full_response)
        if parsed is not None:
            return parsed

        # Fall back to reading a plain-text answer with section headings
        result = {"missing_clauses": [], "compliance_issues": [], "recommendations": [], "summary": ""}
        current_section = None
        for section in full_response.split("\n\n"):
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
                cleaned_text = section.replace("*", "").strip()
                if cleaned_text:
                    result[current_section].append(cleaned_text)

        if not any(result.values()):
            logger.error(f"check_compliance: could not parse model output ({len(full_response)} chars)")
            raise LLMResponseFormatError("Compliance check returned an unexpected format")
        return result


def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Parse a JSON object from the model's answer, also when it's wrapped in prose."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return None


def _retry_after_seconds(response: requests.Response) -> Optional[int]:
    try:
        return max(1, int(float(response.headers.get("retry-after", ""))))
    except ValueError:
        return None


# ---------- Providers ----------

class OllamaClient(LLMClient):
    """A local Ollama server. Free and private: documents never leave your machine."""
    provider = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama2"):
        super().__init__(model)
        self.base_url = base_url

    def _request(self, prompt, system_prompt):
        body = {"model": self.model, "prompt": prompt, "system": system_prompt, "stream": True,
                "options": {"temperature": self.temperature}}
        return f"{self.base_url}/api/generate", body, {}

    def _parse_line(self, line):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Skipping a malformed line from the Ollama stream")
            return None, False
        if data.get("error"):
            raise LLMError(f"Ollama error: {data['error']}")
        return data.get("response") or None, bool(data.get("done"))

    def _raise_for_status(self, response):
        if response.status_code == 404:
            raise ModelNotFoundError(
                f"Model '{self.model}' is not installed",
                user_message=f"Required LLM model is not available. Please run 'ollama pull {self.model}' and try again.",
            )
        super()._raise_for_status(response)

    def _unavailable_error(self):
        return OllamaUnavailableError(
            f"Could not connect to Ollama at {self.base_url}",
            user_message="Ollama server is not running. Please start Ollama and try again.",
        )

    def _check_health_uncached(self):
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            installed = [m.get("name", "") for m in response.json().get("models", [])]
            available = any(name == self.model or name.split(":")[0] == self.model for name in installed)
            return {"reachable": True, "model_available": available}
        except (requests.exceptions.RequestException, ValueError) as e:
            logger.warning(f"Ollama server at {self.base_url} is not reachable: {e}")
            return {"reachable": False, "model_available": False}


class GroqClient(LLMClient):
    """Groq's hosted API (OpenAI-compatible). Groq's terms say it doesn't train on API data."""
    provider = "groq"
    default_base_url = "https://api.groq.com/openai/v1"

    def __init__(self, api_key: str, model: str = "openai/gpt-oss-120b",
                 base_url: str = default_base_url):
        super().__init__(model)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def _request(self, prompt, system_prompt):
        messages = ([{"role": "system", "content": system_prompt}] if system_prompt else []) + \
                   [{"role": "user", "content": prompt}]
        body = {"model": self.model, "messages": messages, "stream": True, "temperature": self.temperature}
        return f"{self.base_url}/chat/completions", body, self._headers()

    def _parse_line(self, line):
        # Server-sent events: "data: {...}" lines, ending with "data: [DONE]"
        text = line.decode("utf-8", errors="replace").strip()
        if not text.startswith("data:"):
            return None, False
        payload = text[len("data:"):].strip()
        if payload == "[DONE]":
            return None, True
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("Skipping a malformed line from the Groq stream")
            return None, False
        if data.get("error"):
            raise LLMError(f"Groq error: {data['error']}")
        choices = data.get("choices") or [{}]
        content = (choices[0].get("delta") or {}).get("content")
        return content or None, choices[0].get("finish_reason") is not None

    def _raise_for_status(self, response):
        if response.status_code in (401, 403):
            logger.error("Groq rejected the API key: check GROQ_API_KEY")
            raise ServiceUnavailableError("Groq rejected the API key",
                                          user_message="The AI service isn't configured correctly. Please try again later.")
        if response.status_code == 404:
            raise ModelNotFoundError(f"Groq model '{self.model}' was not found",
                                     user_message="The AI model isn't available right now. Please try again later.")
        if response.status_code == 400 and "context_length" in response.text:
            raise DocumentTooLongError("Text exceeds the model's context window")
        super()._raise_for_status(response)

    def _check_health_uncached(self):
        if not self.api_key:
            logger.warning("GROQ_API_KEY is not set")
            return {"reachable": False, "model_available": False}
        try:
            response = requests.get(f"{self.base_url}/models", headers=self._headers(), timeout=5)
            if response.status_code in (401, 403):
                logger.error("Groq rejected the API key: check GROQ_API_KEY")
                return {"reachable": False, "model_available": False}
            response.raise_for_status()
            ids = {m.get("id") for m in response.json().get("data", [])}
            return {"reachable": True, "model_available": self.model in ids}
        except (requests.exceptions.RequestException, ValueError) as e:
            logger.warning(f"Groq API is not reachable: {e}")
            return {"reachable": False, "model_available": False}
