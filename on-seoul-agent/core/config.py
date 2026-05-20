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
    redis_socket_connect_timeout: int = 2   # 연결 타임아웃(초) — fail-open 대기 상한
    redis_socket_timeout: int = 2           # 명령 타임아웃(초)

    # Answer Cache
    answer_cache_enabled: bool = True
    answer_cache_ttl: int = 900            # 15분 — 수집 스케줄러 주기보다 짧게
    answer_cache_empty_ttl: int = 300      # 빈 결과 캐시 5분
    answer_cache_eligible_intents: tuple[str, ...] = ("SQL_SEARCH", "VECTOR_SEARCH")

    # Recent Queries (per-room)
    recent_queries_enabled: bool = True
    recent_queries_max: int = 5            # 보관 개수
    recent_queries_ttl: int = 1800         # 30분 슬라이딩 — push 마다 갱신

    # Admin
    admin_internal_token: str = ""         # /admin/* 보호용 공유 토큰

    # LLM — Gemini 우선, GPT 폴백
    llm_provider: str = "gemini"  # gemini | openai

    google_api_key: str | None = None
    gemini_model: str = "gemini-2.0-flash"

    openai_api_key: str | None = None
    gpt_model: str = "gpt-4o-mini"

    # 임베딩 — Gemini, output_dimensionality=768 (DDL vector(768) 기준)
    embedding_model: str = "models/gemini-embedding-2-preview"
    # Gemini Embedding API rate limit (요청/분). 유료: 최대 1500, 무료: 100.
    # 버스트 제거 후 실효 간격 = 60/rpm 초. 무료 티어 안전값: 60 이하 권장.
    gemini_embed_rpm: int = 60

    # 임베딩 동기화 API — /embeddings/services/sync 백그라운드 동시 처리 수
    embedding_sync_concurrency: int = 4


settings = Settings()
