"""
Mottainai IA Layer — Central settings
Loads environment variables and exposes typed settings via pydantic-settings.
"""
from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Text LLM
    llm_provider: str = "groq"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # Ollama Cloud (requires token)
    ollama_api_key: str = ""
    ollama_base_url: str = "https://ollama.com/v1"
    ollama_model: str = "gpt-oss:20b"

    # Ollama local (does not send context to an external provider)
    ollama_local_base_url: str = "http://127.0.0.1:11434/v1"
    ollama_local_model: str = "qwen2.5:7b-instruct"

    # Gemini Vision (shelf analysis)
    gemini_api_key: str = ""
    gemini_vision_model: str = "gemini-2.5-flash"

    # Databases
    postgres_dsn: str = Field(
        default="postgresql+asyncpg://mottainai:mottainai@localhost:5432/mottainai",
        validation_alias=AliasChoices("POSTGRES_DSN", "DATABASE_URL"),
    )
    mongo_uri: str = Field(
        default="mongodb://localhost:27017",
        validation_alias=AliasChoices("MONGO_URI", "MONGO_URL"),
    )
    mongo_db: str = "mottainai"
    redis_url: str = "redis://localhost:6379/0"
    redis_password: str = ""
    redis_max_connections: int = 20
    # 2s was too tight in practice: Docker Desktop's networking on Windows can
    # add enough latency to the first connection that it consistently timed
    # out at ~2.01s even against a healthy local container (verified live).
    # Redis is on the guardrail_entrada rate-limit path, which is NOT
    # fail-open, so a too-tight timeout here fails every chat message.
    redis_connect_timeout_seconds: float = 6.0
    redis_socket_timeout_seconds: float = 6.0
    redis_health_check_interval_seconds: int = 30
    rate_limit_window_seconds: int = 60
    notification_ttl_seconds: int = 604800

    # Embeddings
    embedding_model: str = "all-MiniLM-L6-v2"
    transformers_offline: bool = False

    # External API (Open-Meteo — free, no key required)
    openmeteo_base_url: str = "https://api.open-meteo.com/v1/forecast"

    # Rate limit
    rate_limit_rpm: int = 30
    session_timeout_minutes: int = 60

    # JWT authentication (HS256; secret required outside of the code)
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 60

    # Configurable reference cost. On the Groq free tier, keep both at 0.
    llm_input_cost_per_million_usd: float = 0.0
    llm_output_cost_per_million_usd: float = 0.0

    # Robustness: automatic retries on transient LLM provider failure
    # (timeout, rate limit, 5xx error) with exponential backoff + jitter.
    llm_max_retries: int = 3

    # RAG results cache in Redis — avoids recomputing embeddings/similarity
    # for the same question within the same company. Purely a latency
    # optimization: if Redis is unavailable, RAG keeps working without
    # cache (fail-open).
    rag_cache_ttl_seconds: int = 300

    # External integrations (endpoints blocked without configured tokens)
    mcp_shared_token: str = ""
    a2a_shared_token: str = ""
    mcp_empresa_id: int = 0
    a2a_empresa_id: int = 0
    public_base_url: str = "http://localhost:8000"

    # App
    env: str = "development"
    log_level: str = "INFO"

    @field_validator("gemini_vision_model")
    @classmethod
    def migrate_retired_gemini_vision_model(cls, value: str) -> str:
        """Keeps local installs with the legacy name pointed at an active model."""
        return "gemini-2.5-flash" if value.strip() == "gemini-1.5-flash" else value.strip()

    @field_validator("llm_provider")
    @classmethod
    def validate_llm_provider(cls, value: str) -> str:
        provider = value.strip().lower()
        if provider not in {"groq", "ollama", "ollama_local"}:
            raise ValueError("LLM_PROVIDER must be 'groq', 'ollama' or 'ollama_local'")
        return provider

    @property
    def llm_model_label(self) -> str:
        if self.llm_provider == "ollama_local":
            return f"ollama-local/{self.ollama_local_model}"
        if self.llm_provider == "ollama":
            return f"ollama-cloud/{self.ollama_model}"
        return f"groq/{self.groq_model}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
