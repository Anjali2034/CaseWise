"""GroqClient against a fake OpenAI-compatible server."""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import llm_client as oc
import main
import prompts


def sse(content=None, finish=None):
    choice = {"delta": {"content": content} if content else {}, "finish_reason": finish}
    return b"data: " + json.dumps({"choices": [choice]}).encode() + b"\n\n"


class FakeGroq(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    scenario = "ok"
    calls = 0
    last_body = None
    last_auth = None

    def log_message(self, *args):
        pass

    def _send(self, status, body=b"", headers=None, chunks=None, cut=False):
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if chunks is None:
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for c in chunks:
            self.wfile.write(b"%x\r\n%s\r\n" % (len(c), c))
            self.wfile.flush()
        if cut:
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        self.wfile.write(b"0\r\n\r\n")

    def do_GET(self):  # /models (health check)
        type(self).last_auth = self.headers.get("Authorization")
        if self.scenario == "bad_key":
            return self._send(401, b'{"error":{"message":"Invalid API Key"}}')
        body = json.dumps({"data": [{"id": "openai/gpt-oss-120b"}, {"id": "openai/gpt-oss-20b"}]}).encode()
        self._send(200, body)

    def do_POST(self):  # /chat/completions
        type(self).last_body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        type(self).last_auth = self.headers.get("Authorization")
        type(self).calls += 1
        s, n = self.scenario, self.calls
        try:
            if s == "ok":
                self._send(200, chunks=[sse("Hello "), sse("world"), sse(finish="stop"), b"data: [DONE]\n\n"])
            elif s == "bad_key":
                self._send(401, b'{"error":{"message":"Invalid API Key"}}')
            elif s == "no_model":
                self._send(404, b'{"error":{"message":"model not found"}}')
            elif s == "rate_limited":
                self._send(429, b'{"error":{"message":"rate limit"}}', headers={"Retry-After": "17"})
            elif s == "too_long":
                self._send(400, b'{"error":{"code":"context_length_exceeded","message":"too long"}}')
            elif s == "500_then_ok":
                self._send(500, b"{}") if n == 1 else self._send(200, chunks=[sse("Recovered", "stop"), b"data: [DONE]\n\n"])
            elif s == "cut":
                self._send(200, chunks=[sse("Hel")], cut=True)
            elif s == "error_event":
                self._send(200, chunks=[b'data: {"error": {"message": "overloaded"}}\n\n'])
            elif s == "slow":
                time.sleep(1.5)
                self._send(200, chunks=[sse("late", "stop")])
        except OSError:
            pass


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass


@pytest.fixture(scope="module")
def groq_url():
    server = QuietServer(("127.0.0.1", 0), FakeGroq)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/openai/v1"
    server.shutdown()


def client_for(url, scenario, read_timeout=10):
    FakeGroq.scenario, FakeGroq.calls = scenario, 0
    c = oc.GroqClient(api_key="test-key", base_url=url)
    c.read_timeout, c.retry_delay = read_timeout, 0.01
    return c


def run(url, scenario, **kw):
    c = client_for(url, scenario, **kw)
    out = []
    try:
        for chunk in c._generate_stream("prompt", "system rules"):
            out.append(chunk)
        return "".join(out), None
    except Exception as e:  # noqa: BLE001
        return "".join(out), e


def test_streams_text_and_sends_an_openai_style_request(groq_url):
    text, error = run(groq_url, "ok")
    assert (text, error) == ("Hello world", None)
    body = FakeGroq.last_body
    assert body["model"] == "openai/gpt-oss-120b" and body["stream"] is True
    assert body["messages"] == [{"role": "system", "content": "system rules"}, {"role": "user", "content": "prompt"}]
    assert body["temperature"] == 0.2
    assert FakeGroq.last_auth == "Bearer test-key"


@pytest.mark.parametrize("scenario, error_type", [
    ("bad_key", oc.ServiceUnavailableError),
    ("no_model", oc.ModelNotFoundError),
    ("rate_limited", oc.LLMRateLimitedError),
    ("too_long", oc.DocumentTooLongError),
    ("cut", oc.LLMError),
    ("error_event", oc.LLMError),
])
def test_failures_become_typed_errors_without_retrying(groq_url, scenario, error_type):
    _, error = run(groq_url, scenario)
    assert isinstance(error, error_type), error
    assert FakeGroq.calls == 1


def test_rate_limit_keeps_providers_retry_after(groq_url):
    _, error = run(groq_url, "rate_limited")
    assert error.retry_after == 17


def test_bad_key_message_is_safe_to_show(groq_url):
    _, error = run(groq_url, "bad_key")
    assert error.user_message == "The AI service isn't configured correctly. Please try again later."
    assert "test-key" not in str(error) and "test-key" not in error.user_message


def test_transient_500_is_retried_once(groq_url):
    text, error = run(groq_url, "500_then_ok")
    assert (text, error, FakeGroq.calls) == ("Recovered", None, 2)


def test_slow_model_times_out(groq_url):
    _, error = run(groq_url, "slow", read_timeout=0.3)
    assert isinstance(error, oc.LLMTimeoutError)


def test_unreachable_groq_is_service_unavailable():
    c = oc.GroqClient(api_key="k", base_url="http://127.0.0.1:9/openai/v1")
    c.retry_delay = 0.01
    with pytest.raises(oc.ServiceUnavailableError) as info:
        list(c._generate_stream("p", "s"))
    assert not isinstance(info.value, oc.OllamaUnavailableError)  # no "start Ollama" advice on the live site


def test_health_check(groq_url):
    assert client_for(groq_url, "ok").check_health() == {
        "provider": "groq", "model": "openai/gpt-oss-120b", "reachable": True, "model_available": True}
    assert client_for(groq_url, "bad_key").check_health()["reachable"] is False
    assert oc.GroqClient(api_key="", base_url=groq_url).check_health()["reachable"] is False


def test_health_check_is_cached_so_it_does_not_spend_quota(groq_url):
    c = client_for(groq_url, "ok")
    first = c.check_health()
    FakeGroq.scenario = "bad_key"
    assert c.check_health() == first        # served from the cache
    c.health_cache_seconds = 0
    assert c.check_health()["reachable"] is False


@pytest.mark.parametrize("tool, prompt", [("legal_assistant", prompts.LEGAL_ASSISTANT),
                                           ("generate_document", prompts.DOCUMENT_GENERATOR),
                                           ("simplify_document", prompts.DOCUMENT_SIMPLIFIER)])
def test_every_tool_works_through_groq(groq_url, tool, prompt):
    assert getattr(client_for(groq_url, "ok"), tool)("some text") == "Hello world"
    assert FakeGroq.last_body["messages"][0] == {"role": "system", "content": prompt}


# ---------- provider selection in main.py ----------

def test_text_limit_default_depends_on_provider():
    """Groq's free plan: 8,000 tokens/minute, and one request must fit in that."""
    assert main.DEFAULT_MAX_TEXT_CHARS == {"groq": 20000, "ollama": 50000}
    assert oc.GroqClient(api_key="k").model == "openai/gpt-oss-120b"


def test_provider_selection(monkeypatch):
    monkeypatch.setattr(main, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(main, "GROQ_API_KEY", "k")
    client = main.make_llm_client()
    assert isinstance(client, oc.GroqClient) and client.model == main.GROQ_MODEL
    monkeypatch.setattr(main, "LLM_PROVIDER", "ollama")
    assert isinstance(main.make_llm_client(), oc.OllamaClient)
    monkeypatch.setattr(main, "LLM_PROVIDER", "openai")
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        main.make_llm_client()


def test_groq_errors_reach_users_with_neutral_messages(client, monkeypatch, groq_url):
    monkeypatch.setattr(main, "llm", client_for(groq_url, "bad_key"))
    r = client.post("/legal-assistant", json={"question": "q"})
    assert r.status_code == 503 and "isn't configured correctly" in r.json()["detail"]
    monkeypatch.setattr(main, "llm", client_for(groq_url, "rate_limited"))
    r = client.post("/legal-assistant", json={"question": "q"})
    assert r.status_code == 429 and r.headers["Retry-After"] == "17"
    monkeypatch.setattr(main, "llm", client_for(groq_url, "ok"))
    assert client.post("/legal-assistant", json={"question": "q"}).json() == {"response": "Hello world"}
