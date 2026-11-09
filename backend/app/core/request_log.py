"""Request ids, one access-log line per request, and a catch-all for unexpected errors."""

import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.client_ip import Network, client_ip
from app.core.errors import error_response
from app.core.logging import log_unexpected, request_id_var

logger = logging.getLogger("app.request")
error_logger = logging.getLogger("app.errors")

VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")


def pick_request_id(supplied: str | None) -> str:
    """A client's id is kept only when harmless to put in a log line."""
    if supplied is not None and VALID_REQUEST_ID.fullmatch(supplied):
        return supplied
    return uuid.uuid4().hex


class RequestLogMiddleware:
    """Outermost layer, so every response (refusals included) gets an id and a line.

    The line holds the path but never the query string, headers or body.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        quiet_paths: frozenset[str],
        trusted_proxies: tuple[Network, ...] = (),
    ) -> None:
        self.app = app
        self.quiet_paths = quiet_paths
        self.trusted_proxies = trusted_proxies

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        supplied = headers.get(b"x-request-id")
        request_id = pick_request_id(supplied.decode("latin-1") if supplied else None)
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            level = (
                logging.ERROR
                if status >= 500
                else logging.DEBUG
                if scope["path"] in self.quiet_paths
                else logging.INFO
            )
            logger.log(
                level,
                "request_id=%s method=%s path=%s status=%s duration_ms=%s client=%s",
                request_id,
                scope["method"],
                scope["path"],
                status,
                elapsed_ms,
                client_ip(Request(scope), self.trusted_proxies),
            )
            request_id_var.reset(token)


class UnexpectedErrorMiddleware:
    """Innermost layer: turns an unexpected exception into the generic 500 inside the
    other layers, so the response still gets the id and the security headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            log_unexpected(
                error_logger,
                "Unexpected error",
                exc,
                method=scope["method"],
                path=scope["path"],
            )
            if started:
                raise
            response = error_response(
                500, "internal_error", "An internal error occurred."
            )
            await response(scope, receive, send)
