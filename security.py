"""Security headers on every response, including a strict Content-Security-Policy.

Each request gets a random nonce. Only inline <script>/<style> blocks carrying that
nonce run, so even if AI output or an attacker managed to inject markup, the browser
would refuse to execute it. The policy also blocks images from other sites, which
stops a malicious AI answer from leaking data through an image URL.
"""
import secrets
from typing import Dict

from starlette.datastructures import Headers, MutableHeaders

# FastAPI's interactive API docs load their scripts from a CDN, so they can't use the strict policy
DOCS_PATHS = ("/docs", "/redoc")


def content_security_policy(nonce: str) -> str:
    return "; ".join([
        "default-src 'self'",
        f"script-src 'self' 'nonce-{nonce}'",
        f"style-src 'self' 'nonce-{nonce}'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])


def security_headers(nonce: str, path: str, https: bool) -> Dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    }
    if not path.startswith(DOCS_PATHS):
        headers["Content-Security-Policy"] = content_security_policy(nonce)
    if https:
        headers["Strict-Transport-Security"] = "max-age=31536000"
    return headers


def request_is_https(scope) -> bool:
    """Behind a proxy like Render the app sees plain http; the proxy says what the visitor used."""
    forwarded = Headers(scope=scope).get("x-forwarded-proto", "")
    return scope.get("scheme") == "https" or forwarded.split(",")[0].strip() == "https"


class SecurityHeadersMiddleware:
    """Plain ASGI middleware, so streamed answers pass through without being buffered."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        nonce = secrets.token_urlsafe(16)
        # Templates read this as request.state.csp_nonce
        scope.setdefault("state", {})["csp_nonce"] = nonce
        extra = security_headers(nonce, scope["path"], request_is_https(scope))

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).update(extra)
            await send(message)

        await self.app(scope, receive, send_with_headers)
