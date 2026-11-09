from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import check_production_settings, get_settings
from app.core.errors import register_error_handlers
from app.core.http_hardening import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.core.ratelimit import GlobalRateLimitMiddleware, RateLimiter
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
    # The last middleware added is the outermost. From the outside in: security headers,
    # CORS (so refusals below still carry CORS headers), the rate limit (every request
    # counts), then the body-size check.
    app.add_middleware(
        BodySizeLimitMiddleware, max_bytes=settings.max_request_body_kb * 1024
    )
    app.add_middleware(
        GlobalRateLimitMiddleware,
        exempt_paths=frozenset(
            {f"{prefix}/health", "/docs", "/redoc", "/openapi.json"}
        ),
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
            allow_credentials=False,
            max_age=600,
        )
    app.add_middleware(
        SecurityHeadersMiddleware,
        production=settings.environment == "production",
        csp_exempt_paths=frozenset({"/docs", "/redoc", "/openapi.json"}),
    )
    register_error_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app
