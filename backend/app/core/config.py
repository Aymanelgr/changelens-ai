"""Application settings, read only from environment variables (or a local .env)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    # No default on purpose: the app refuses to start with a missing or short secret.
    jwt_secret: str = Field(min_length=32)
    jwt_expire_minutes: int = 120

    storage_dir: str = "./storage"

    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""

    # DATA-002: limits on untrusted uploads.
    max_document_bytes: int = 20 * 1024 * 1024
    max_archive_bytes: int = 50 * 1024 * 1024
    max_archive_files: int = 2000
    max_archive_uncompressed_bytes: int = 200 * 1024 * 1024
    max_python_file_bytes: int = 1024 * 1024

    max_pdf_pages: int = 500
    max_candidates_per_requirement: int = 20

    # Search (pipeline steps 5 and 6). The two thresholds were fixed on the development
    # scenarios only (AI-010): see evaluation/README.md.
    embedding_provider: Literal["sentence-transformers", "none"] = "sentence-transformers"
    embedding_model: str = "BAAI/bge-m3"
    search_top_k: int = 10
    search_min_cosine: float = 0.50
    search_strong_cosine: float = 0.60

    # LLM (pipeline steps 7 and 8). The model is replaceable: the code only knows LLMClient.
    llm_provider: Literal["ollama", "remote", "none"] = "ollama"
    llm_model: str = "qwen3:8b"
    # Models an analyst may pick for one run (FR-020), comma-separated. Empty: the default only.
    llm_allowed_models: str = ""
    llm_temperature: float = 0.0
    llm_seed: int = 42
    llm_timeout_seconds: int = 300
    llm_context_tokens: int = 8192
    llm_batch_size: int = 6
    llm_excerpt_chars: int = 700
    ollama_url: str = "http://ollama:11434"

    # Optional hosted API, synthetic data only (section 4.8). Any OpenAI-compatible endpoint.
    remote_api_url: str = ""
    remote_api_key: str = ""
    remote_input_cost_per_mtok: float = 0.0
    remote_output_cost_per_mtok: float = 0.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
