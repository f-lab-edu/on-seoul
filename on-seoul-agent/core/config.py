from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    app_name: str = "on-seoul-agent"
    app_version: str = "0.1.0"
    debug: bool = False

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    # Database (PostgreSQL)
    # on_ai_database_url  : on_ai DB — AI 서비스 전용 (service_embeddings, chat_agent_traces). CRUD 권한.
    # on_data_database_url: on_data DB — 정형 데이터 (public_service_reservations 등). SELECT 전용 계정.
    on_ai_database_url: str
    on_data_database_url: str

    # Redis
    redis_url: str = "redis://localhost:6379"

    # LLM — Gemini 우선, GPT 폴백
    llm_provider: str = "gemini"  # gemini | openai

    google_api_key: str | None = None
    gemini_model: str = "gemini-2.0-flash"

    openai_api_key: str | None = None
    gpt_model: str = "gpt-4o-mini"

    # 임베딩 — Gemini, output_dimensionality=1536 (DDL vector(1536) 기준)
    embedding_model: str = "models/gemini-embedding-2-preview"
    # Gemini Embedding API rate limit (요청/분). 유료: 최대 1500, 무료: 100.
    # 버스트 제거 후 실효 간격 = 60/rpm 초. 무료 티어 안전값: 60 이하 권장.
    gemini_embed_rpm: int = 60


settings = Settings()
