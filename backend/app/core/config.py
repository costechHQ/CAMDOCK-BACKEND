from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: SecretStr
    api_v1_prefix: str = "/api/v1"
    session_ttl_hours: int = Field(default=12, gt=0)
    # Printed on result sheets; the defaults are the values the SRS confirms.
    school_name: str = Field(default="Camdock International School", min_length=1)
    school_location: str = Field(default="Enugu State, Nigeria", min_length=1)

    @property
    def docs_enabled(self) -> bool:
        return self.environment == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
