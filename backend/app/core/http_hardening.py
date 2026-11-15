"""Response headers and request-size limits applied to every HTTP request."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_response

BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # Responses carry personal and financial data, so shared caches must not keep them.
    "Cache-Control": "no-store",
}
# The API serves JSON only, so it never needs to load or embed anything.
API_CSP = "default-src 'none'; frame-ancestors 'none'"
HSTS = "max-age=31536000; includeSubDomains"


class SecurityHeadersMiddleware:
    """Adds the security headers to every response, errors and rate-limit refusals
    included. HSTS is sent only in production, so local HTTP use is not affected, and
    the CSP skips the development-only docs pages, which need scripts."""

    def __init__(
        self, app: ASGIApp, *, production: bool, csp_exempt_paths: frozenset[str]
    ) -> None:
        self.app = app
        self.production = production
        self.csp_exempt_paths = csp_exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in BASE_HEADERS.items():
                    headers.setdefault(name, value)
                if path not in self.csp_exempt_paths:
                    headers.setdefault("Content-Security-Policy", API_CSP)
                if self.production:
                    headers.setdefault("Strict-Transport-Security", HSTS)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodySizeLimitMiddleware:
    """Refuses oversized bodies from the declared length, before reading any of it.

    The HTTP layer enforces the declared length, so a client cannot send more than it
    declares. A body with no declared length (chunked) cannot be checked up front and
    is refused with 411.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {name.lower(): value for name, value in scope["headers"]}
        refusal = self._refusal(headers.get(b"content-length"), headers)
        if refusal is not None:
            await refusal(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _refusal(self, length: bytes | None, headers: dict[bytes, bytes]):
        if length is None:
            if b"transfer-encoding" in headers:
                return error_response(
                    411,
                    "length_required",
                    "Send a Content-Length header with the request body.",
                )
            return None
        if not length.isdigit():
            return error_response(400, "bad_request", "Invalid Content-Length.")
        if int(length) > self.max_bytes:
            return error_response(
                413, "payload_too_large", "The request body is too large."
            )
        return None
