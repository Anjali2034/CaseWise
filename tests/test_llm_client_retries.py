"""OllamaClient._generate_stream against a fake Ollama HTTP server (fix #6)."""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import llm_client as oc


def chunk(text, done=False):
    return json.dumps({"response": text, "done": done}).encode() + b"\n"


class FakeOllama(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    scenario = "ok"
    calls = 0

    def log_message(self, *args):
        pass

    def _chunked(self, parts, cut=False):
        self.send_response(200)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for p in parts:
            self.wfile.write(b"%x\r\n%s\r\n" % (len(p), p))
            self.wfile.flush()
        if cut:  # drop the connection mid-stream
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        self.wfile.write(b"0\r\n\r\n")

    def _status(self, code):
        body = b'{"error":"x"}'
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        type(self).calls += 1
        s, n = self.scenario, self.calls
        try:
            if s == "ok":
                self._chunked([chunk("Hello "), chunk("world"), chunk("!", True)])
            elif s == "cut_mid_stream":
                self._chunked([chunk("Hello "), chunk("wor")], cut=True)
            elif s == "404":
                self._status(404)
            elif s == "500_then_ok":
                self._status(500) if n == 1 else self._chunked([chunk("Recovered", True)])
            elif s == "500_always":
                self._status(500)
            elif s == "error_line":
                self._chunked([json.dumps({"error": "model crashed"}).encode() + b"\n"])
            elif s == "slow":
                time.sleep(1.5)
                self._chunked([chunk("late", True)])
        except OSError:
            pass  # client gave up (timeout test)


@pytest.fixture(scope="module")
def fake_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def run(base_url, scenario, read_timeout=10):
    FakeOllama.scenario, FakeOllama.calls = scenario, 0
    client = oc.OllamaClient(base_url=base_url)
    client.read_timeout = read_timeout
    client.retry_delay = 0.01
    received = []
    try:
        for c in client._generate_stream("prompt"):
            received.append(c)
        return "".join(received), None, FakeOllama.calls
    except Exception as e:  # noqa: BLE001
        return "".join(received), e, FakeOllama.calls


def test_normal_stream(fake_server):
    text, error, calls = run(fake_server, "ok")
    assert (text, error, calls) == ("Hello world!", None, 1)


def test_stream_cut_mid_way_is_not_retried_so_no_duplicate_text(fake_server):
    text, error, calls = run(fake_server, "cut_mid_stream")
    assert isinstance(error, oc.LLMError)
    assert calls == 1
    assert text.count("Hello") <= 1


def test_404_model_missing_fails_immediately(fake_server):
    _, error, calls = run(fake_server, "404")
    assert isinstance(error, oc.ModelNotFoundError)
    assert calls == 1


def test_transient_500_is_retried_once_and_recovers(fake_server):
    text, error, calls = run(fake_server, "500_then_ok")
    assert (text, error, calls) == ("Recovered", None, 2)


def test_persistent_500_gives_up_after_two_attempts(fake_server):
    _, error, calls = run(fake_server, "500_always")
    assert isinstance(error, oc.LLMError)
    assert calls == 2


def test_error_line_inside_stream_is_reported(fake_server):
    _, error, _ = run(fake_server, "error_line")
    assert isinstance(error, oc.LLMError) and "model crashed" in str(error)


def test_slow_model_times_out_without_retry(fake_server):
    _, error, calls = run(fake_server, "slow", read_timeout=0.3)
    assert isinstance(error, oc.LLMTimeoutError)
    assert calls == 1


def test_ollama_down_raises_unavailable():
    client = oc.OllamaClient(base_url="http://127.0.0.1:9")
    client.retry_delay = 0.01
    with pytest.raises(oc.OllamaUnavailableError):
        list(client._generate_stream("prompt"))
