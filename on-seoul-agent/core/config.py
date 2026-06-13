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

    # Answer Cache Singleflight (0-3-2) — flush 후 thundering herd 방지.
    # 동시 cold miss 시 첫 호출자만 LLM을 실행하고 나머지는 결과를 기다린다.
    # Redis 장애 시 fail-open: 각자 LLM 호출(last-write-wins, 정합성 유지).
    answer_cache_singleflight_enabled: bool = True
    # lock TTL — LLM 응답 여유(일반 ~3s, 최대 ~10s + 마진). 만료되면 waiter가 fail-open.
    answer_cache_lock_ttl: int = 30
    # waiter 재시도: retries × interval 초 동안 캐시 키를 주기 재조회.
    # 6 × 0.5 = 3s: 정상 LLM 응답 내 결과를 받아 hit으로 전환.
    answer_cache_lock_poll_retries: int = 6
    answer_cache_lock_poll_interval: float = 0.5

    # Refine Cache (0-3-3) — router_node LLM(검색 계획 수립) 결과 캐시.
    # 키 = 정규화 raw query (+ history 해시). history 없으면 first-turn 사용자 간 공유.
    # answer_cache 와 별개 네임스페이스(refine_cache:).
    refine_cache_enabled: bool = True
    # refine 출력(query→intent/필터 매핑)은 **데이터 비의존**이다: 예약 데이터가 바뀌어도
    # 동일 질의의 검색 계획은 불변. 따라서 answer_cache_ttl(15분, 데이터 의존)보다 길게 둔다.
    # 6시간 — 프롬프트/모델 변경 시 자연 만료로 점진 반영되도록 무한이 아닌 장기값.
    refine_cache_ttl: int = 21600
    # flush 비대상: 데이터 비의존이라 수집 무효화(/admin/cache/flush, answer_cache 한정)와
    # 무관하다. refine_cache:* 는 flush_answer_cache 스캔 패턴(answer_cache:*)에 걸리지 않는다.

    # Admin
    admin_internal_token: str = ""  # /admin/* 보호용 공유 토큰

    # ------------------------------------------------------------------
    # OpenTelemetry (SigNoz, OTLP gRPC 4317)
    # ------------------------------------------------------------------
    # infra 핸드오프 — docker-compose에서 주입할 환경변수(키 = 대문자 필드명):
    #   OTEL_ENABLED=true
    #   OTEL_EXPORTER_OTLP_ENDPOINT=http://on-seoul-signoz:4317
    #   OTEL_SERVICE_NAME=on-seoul-agent
    #   OTEL_ENVIRONMENT=prod                (선택, 기본 "local")
    #   OTEL_EXPORTER_OTLP_TIMEOUT=10        (선택, 초)
    #   OTEL_METRIC_EXPORT_INTERVAL_MS=60000 (선택, ms)
    # 기본 off — 명시적으로 OTEL_ENABLED=true 를 줘야 계측이 동작한다.
    # endpoint가 비어 있으면 enabled여도 no-op(fail-open).
    otel_enabled: bool = False
    otel_service_name: str = "on-seoul-agent"
    otel_exporter_otlp_endpoint: str = "http://on-seoul-signoz:4317"
    otel_environment: str = "local"
    otel_exporter_otlp_timeout: int = 10  # gRPC export 타임아웃(초)
    otel_metric_export_interval_ms: int = 60000  # 메트릭 주기 export 간격(ms)

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

    # VECTOR 4채널 동시성 상한 — asyncio.Semaphore 값.
    # 채널 수(4)와 동일하게 두면 풀 max_overflow(15) 이내에서 버스트 안전.
    vector_channel_concurrency: int = 4

    # 글로벌 VECTOR fan-out 세마포어 — 고QPS 환경에서 풀 고갈 방지.
    # 100 동시 요청 × 4채널 = 400 동시 on_ai 쿼리 가능성을 막아
    # on_ai 풀(pool_size=10, max_overflow=15 → cap=25)이 고갈되지 않게 한다.
    # 20으로 설정하면 동시 채널 쿼리 ≤ 20 → 풀 cap(25) 이내 유지.
    vector_global_concurrency: int = 20

    # httpx HTTP 연결 풀 — LLM 클라이언트용 (LangChain SDK 전달).
    # 컨테이너당 answer 스트림 ≈ 100 × 3s = 300, router ≈ 100 × 0.5s = 50
    # → 동시 LLM HTTP 연결 ~350. httpx 기본 max_connections=100 으로는 부족.
    llm_http_max_connections: int = 400
    # 임베딩 클라이언트 — 동시 임베딩 요청 ≈ 100/s (vector agent 1건/요청).
    embedding_http_max_connections: int = 200

    # Triple-track + RRF 결합
    rrf_k_constant: int = 60
    # post-filter 적용 측정에서 깊이30/scan100의 recall@k 이득 미확인 → 두 값 모두 원본 유지.
    #   rrf_scan_k_per_track: 트랙당 ANN 1차 스캔 깊이 (top_k보다 커 post-filter 탈락 완충).
    #   vector_track_top_k  : 트랙별 RRF 입력 깊이.
    rrf_scan_k_per_track: int = 50
    rrf_top_k_final: int = 10
    vector_track_top_k: int = 10

    # 트랙별 코사인 유사도 하한 — 3트랙 공통 0.65 uniform.
    # 하한 0.55/0.60/0.65/0.70 스윕에서 0.65가 정점(역U자), 0.70 급락,
    # identification recall 1.0 유지 — 2026-06 측정. recall@k 기준.
    # 현재 3트랙 동일(0.65)이나, 트랙별로 다르게 둘 수 있는 구조(3개 키)는
    # 향후 트랙별 조정 여지를 위해 유지한다.
    vector_min_similarity_identity: float = 0.65
    vector_min_similarity_summary: float = 0.65
    vector_min_similarity_question: float = 0.65

    # secondary_intent 팬아웃 단계적 롤아웃 플래그.
    # False(기본): primary_intent만으로 단일 라우트(기존 동작과 완전 동일).
    # True: secondary_intent가 있을 때 SQL+VECTOR 병렬 팬아웃 → RRF fusion.
    # 활성화 전제: TriageAgent secondary_intent 분류 정확도 검증 후 수동 전환.
    enable_secondary_intent: bool = False

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
