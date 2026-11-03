"""Shared test setup.

Tests never need a real Ollama server: the app is pointed at a closed port, and
each test either mocks `_generate_stream` or starts a small fake Ollama server.
"""
import io
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # the app mounts "static" and "templates" relative to the cwd

# Must be set before `main` is imported. Port 9 is closed, so Ollama is "down".
os.environ["OLLAMA_API_BASE"] = "http://127.0.0.1:9"
os.environ["OLLAMA_MODEL"] = "llama2"

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def llm_reply():
    """Make the model 'reply' with the given chunks (or raise the given exception)."""
    patches = []

    def _set(*chunks, error=None):
        kwargs = {"side_effect": error} if error else {"side_effect": lambda *a, **k: iter(chunks)}
        p = mock.patch.object(main.llm, "_generate_stream", **kwargs)
        p.start()
        patches.append(p)

    yield _set
    for p in patches:
        p.stop()


def make_text_pdf(text: str) -> bytes:
    """Build a minimal valid one-page PDF containing `text`."""
    stream = f"BT /F1 12 Tf 10 50 Td ({text}) Tj ET".encode()
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 100]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>stream\n" % len(stream) + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj" % number + body + b"endobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % (len(objects) + 1, xref)
    return bytes(out)


def make_blank_pdf() -> bytes:
    from PyPDF2 import PdfWriter
    buf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    writer.write(buf)
    return buf.getvalue()
