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
    redis_socket_connect_timeout: int = 2  # 연결 타임아웃(초) — fail-open 대기 상한
    redis_socket_timeout: int = 2  # 명령 타임아웃(초)

    # Answer Cache
    answer_cache_enabled: bool = True
    answer_cache_ttl: int = 900  # 15분 — 수집 스케줄러 주기보다 짧게
    answer_cache_empty_ttl: int = 300  # 빈 결과 캐시 5분
    answer_cache_eligible_intents: tuple[str, ...] = ("SQL_SEARCH", "VECTOR_SEARCH")

    # Recent Queries (per-room)
    recent_queries_enabled: bool = True
    recent_queries_max: int = 5  # 보관 개수
    recent_queries_ttl: int = 1800  # 30분 슬라이딩 — push 마다 갱신

    # Admin
    admin_internal_token: str = ""  # /admin/* 보호용 공유 토큰

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

    # Triple-track + RRF 결합
    rrf_k_constant: int = 60
    rrf_scan_k_per_track: int = 50
    rrf_top_k_final: int = 10

    # VectorSubIntent 활성화 단계
    # False(기본): 항상 vector_default_sub_intent 프로파일 사용.
    # True 전환 조건: Router의 sub_intent 분류 정확도 ≥ 80% 검증 후 수동 전환. (Phase 3)
    vector_sub_intent_enabled: bool = False
    # rrf_weight_profiles에 반드시 존재하는 키여야 한다 (없으면 equal-weight로 폴백).
    vector_default_sub_intent: str = "semantic"

    # 가중치 프로파일 — sub_intent → {track_a, track_b, track_c, bm25}
    # 평가셋(scripts/eval) 측정 후 사람이 수동 반영. 코드에 직접 박지 않는다.
    rrf_weight_profiles: dict[str, dict[str, float]] = {
        "identification": {
            "track_a": 0.5,
            "track_b": 0.25,
            "track_c": 0.25,
            "bm25": 0.5,
        },
        "detail": {"track_a": 0.2, "track_b": 0.5, "track_c": 0.3, "bm25": 0.4},
        "semantic": {"track_a": 0.15, "track_b": 0.35, "track_c": 0.5, "bm25": 0.3},
    }

    # Phase 1 baseline 모드: True → 모든 채널 가중치 1.0 (비가중치 RRF).
    # False 전환 조건: recall@k baseline 측정 완료 후 가중치 활성화. (Phase 2)
    rrf_unweighted_baseline: bool = True


settings = Settings()
