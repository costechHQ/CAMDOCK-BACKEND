from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.client_ip import Network, parse_networks


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: SecretStr
    api_v1_prefix: str = "/api/v1"
    session_ttl_hours: int = Field(default=12, gt=0)
    # Failed sign-ins: this many inside the window blocks that email (or address).
    login_max_failures: int = Field(default=5, ge=1)
    login_ip_max_failures: int = Field(default=20, ge=1)
    login_window_minutes: int = Field(default=15, ge=1)
    login_block_minutes: int = Field(default=15, ge=1)
    # Comma-separated proxy addresses or CIDR ranges whose X-Forwarded-For is believed.
    trusted_proxies: str = ""
    # Printed on result sheets; the defaults are the values the SRS confirms.
    school_name: str = Field(default="Camdock International School", min_length=1)
    school_location: str = Field(default="Enugu State, Nigeria", min_length=1)

    @field_validator("trusted_proxies")
    @classmethod
    def _valid_proxies(cls, value: str) -> str:
        try:
            parse_networks(value)
        except ValueError:
            raise ValueError(
                "must be comma-separated IP addresses or CIDR ranges"
            ) from None
        return value

    @property
    def trusted_proxy_networks(self) -> tuple[Network, ...]:
        return parse_networks(self.trusted_proxies)

    @property
    def docs_enabled(self) -> bool:
        return self.environment == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
