import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import check_production_settings, get_settings
from app.core.errors import register_error_handlers
from app.core.http_hardening import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.core.logging import configure_logging
from app.core.ratelimit import GlobalRateLimitMiddleware, RateLimiter
from app.core.request_log import RequestLogMiddleware, UnexpectedErrorMiddleware
from app.core.throttle import FailureThrottle, LoginThrottle
from app.routers.router import api_router


def create_app() -> FastAPI:
    settings = get_settings()
    check_production_settings(settings)
    app = FastAPI(
        title="Camdock International School Portal API",
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    window = settings.login_window_minutes * 60
    block = settings.login_block_minutes * 60
    app.state.login_throttle = LoginThrottle(
        FailureThrottle(settings.login_max_failures, window, block),
        FailureThrottle(settings.login_ip_max_failures, window, block),
    )
    app.state.public_limiter = RateLimiter(settings.rate_limit_public_per_minute)
    app.state.global_limiter = RateLimiter(settings.rate_limit_global_per_minute)
    prefix = settings.api_v1_prefix
    configure_logging(settings.environment, settings.log_level)
    # Our access line replaces uvicorn's, which prints the full URL with its query string.
    logging.getLogger("uvicorn.access").disabled = True
    # The last middleware added is the outermost. From the outside in: request log (id
    # and access line), security headers, CORS (so refusals below still carry CORS
    # headers), the rate limit (every request counts), the body-size check, then the
    # catch-all that turns unexpected errors into a 500.
    app.add_middleware(UnexpectedErrorMiddleware)
    app.add_middleware(
        BodySizeLimitMiddleware, max_bytes=settings.max_request_body_kb * 1024
    )
    app.add_middleware(
        GlobalRateLimitMiddleware,
        exempt_paths=frozenset(
            {
                f"{prefix}/health",
                f"{prefix}/health/ready",
                "/docs",
                "/redoc",
                "/openapi.json",
            }
        ),
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
            allow_credentials=False,
            expose_headers=["X-Request-ID", "Retry-After"],
            max_age=600,
        )
    app.add_middleware(
        SecurityHeadersMiddleware,
        production=settings.environment == "production",
        csp_exempt_paths=frozenset({"/docs", "/redoc", "/openapi.json"}),
    )
    app.add_middleware(
        RequestLogMiddleware,
        quiet_paths=frozenset({f"{prefix}/health", f"{prefix}/health/ready"}),
        trusted_proxies=settings.trusted_proxy_networks,
    )
    register_error_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


_app: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    """Build `app` on first use so `fastapi dev` and `uvicorn app.main:app` can find it
    without the app (and its settings) being created whenever this module is imported."""
    global _app
    if name != "app":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if _app is None:
        _app = create_app()
    return _app


def __dir__() -> list[str]:
    return [*globals(), "app"]
