from fastapi import FastAPI

from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.ratelimit import GlobalRateLimitMiddleware, RateLimiter
from app.core.throttle import FailureThrottle, LoginThrottle
from app.routers.router import api_router


def create_app() -> FastAPI:
    settings = get_settings()
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
    app.add_middleware(
        GlobalRateLimitMiddleware,
        exempt_paths=frozenset(
            {f"{prefix}/health", "/docs", "/redoc", "/openapi.json"}
        ),
    )
    register_error_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app
