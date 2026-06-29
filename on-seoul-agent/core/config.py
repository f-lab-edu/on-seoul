from pydantic import AliasChoices, Field
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
    # 일관 모델: poll_window(≈p95 답변 생성시간, ~10s) < lock_ttl(worst-case 30s).
    #   - lock_ttl: 락 보유자의 worst-case 답변 생성 + 캐시 쓰기 + 마진. 락의 자동
    #     만료 상한일 뿐, waiter 의 대기 상한이 아니다(아래 poll_window 가 그 역할).
    #   - poll_window = poll_retries × poll_interval: waiter 가 캐시 키를 재조회하는
    #     총 대기 시간. **poll_window 초과 시(만료가 아니라) waiter 가 fail-open** 하여
    #     각자 LLM 을 실행한다. 보유자는 답변 *완료* 시점에 캐시를 쓰고 락을 DEL 하므로
    #     (스트리밍), waiter 는 보유자 답변 생성시간만큼 폴해야 hit 한다.
    # 불변식: poll_window(10s) < lock_ttl(30s) — 락이 살아있는 동안만 폴한다.
    answer_cache_lock_ttl: int = (
        30  # worst-case 답변 + 쓰기 + 마진. 변경 시 위 불변식 확인.
    )
    # waiter 재시도: retries × interval 초 동안 캐시 키를 주기 재조회.
    # 20 × 0.5 = 10s: config 가 명시한 최대 답변 시간(~10s)을 커버해 꼬리 herd 까지 방어.
    # 폴은 CacheCheckNode(검색 이전 단계)에서 일어나 DB 세션을 점유하지 않으므로
    # (asyncio.sleep + redis GET 만) 윈도우 연장 비용이 낮다.
    # 보수적 기본값 — **정밀 값은 실 p95 답변 생성시간 측정 후 재조정** 권장.
    answer_cache_lock_poll_retries: int = 20
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

    # Refine Cache Singleflight — refine hop(router_node classify) thundering herd 방지.
    # answer singleflight 와 대칭이되, refine LLM 은 ~0.5s 로 answer(~10s)보다 훨씬
    # 빠르므로 노브를 별도로 둔다(짧은 TTL·짧은 poll 윈도우).
    # Redis 장애 또는 poll 윈도우 초과 시 fail-open: 각자 classify 실행.
    # refine 은 temperature=0 결정론 → 중복 결과 동일 → last-write-wins 정합 안전.
    refine_cache_singleflight_enabled: bool = True
    # 불변식: poll_window(2.0s) < lock_ttl(10s) — 락이 살아있는 동안만 폴한다.
    #   - lock_ttl: refine LLM worst-case + 마진. answer(30)보다 훨씬 짧게.
    #   - poll_window = poll_retries × poll_interval = 10 × 0.2 = 2.0s.
    refine_cache_lock_ttl: int = 10  # refine worst-case + 마진. 변경 시 위 불변식 확인.
    # 보수적 기본값 — **정밀 값은 실 p95 refine 생성시간 측정 후 재조정** 권장.
    refine_cache_lock_poll_retries: int = 10
    refine_cache_lock_poll_interval: float = 0.2

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

    # ------------------------------------------------------------------
    # Langfuse (LLM 관측가능성 — Langfuse Cloud, OTel 과 별개 파이프라인)
    # ------------------------------------------------------------------
    # 그래프 실행 경로의 LLM I/O·토큰·비용을 LangChain CallbackHandler 로 관측한다.
    # 인프라 계측(OTel→SigNoz)은 위 섹션이, LLM 계측은 core/langfuse_client.py 가 담당.
    # infra 핸드오프 — docker-compose에서 주입할 환경변수(키 = 대문자 필드명):
    #   LANGFUSE_ENABLED=true
    #   LANGFUSE_PUBLIC_KEY=pk-lf-...        (.env 주입, 커밋 금지)
    #   LANGFUSE_SECRET_KEY=sk-lf-...        (.env 주입, 커밋 금지)
    #   LANGFUSE_BASE_URL=https://cloud.langfuse.com  (선택, LANGFUSE_HOST 도 허용)
    # 기본 off — 명시적으로 LANGFUSE_ENABLED=true + 키를 줘야 동작한다.
    # 키가 비어 있으면 enabled여도 no-op(fail-open).
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    # host env 이름: 현행 SDK 표준은 LANGFUSE_BASE_URL 이지만 LANGFUSE_HOST 도 인식한다.
    # 실제 .env 는 LANGFUSE_BASE_URL=https://jp.cloud.langfuse.com (JP 리전).
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        validation_alias=AliasChoices("LANGFUSE_BASE_URL", "LANGFUSE_HOST"),
    )

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

    # 글로벌 VECTOR fan-out 세마포어 — 고QPS 환경에서 풀 고갈 방지.
    # 단일 인스턴스 200 QPS 기준(Little's Law L=λ×W로 재산정).
    # 200 QPS × 4채널 fan-out을 풀 cap 50 이내로 제한하고, persist/trace 여유 ~10 확보.
    # vector 채널 λ=800/s, W=0.02s → 평균~16, 피크(p99≈3×)~48. 동시 채널 쿼리 ≤ 40.
    # 글로벌 cap(40) < on_ai 풀 cap(50) 불변식 유지(여유분이 persist/trace 흡수).
    vector_global_concurrency: int = 40

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

    # HNSW ANN 후보 탐색 폭. min_similarity 를 outer 필터로 옮겨 HNSW Index Scan 을
    # 타게 한 뒤, 후보 LIMIT(scan_k) 만큼 실제로 채우려면 ef_search >= scan_k 여야 한다.
    # ef_search < scan_k 이면 ANN 이 scan_k 개를 못 채워 truncated/빈 결과가 나와
    # recall 이 하락한다(ef_search=40 < scan_k=50 에서 실측 18/20 불일치). scan_k 의
    # 2배 헤드룸을 둬 exact KNN 과 동일한 결과를 보장한다(ef_search=100 에서 0/20 불일치).
    hnsw_ef_search: int = 100

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
    # True 전환 조건: Router의 sub_intent 분류 정확도 ≥ 80% 검증 후 수동 전환.
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
        # attribute_gap 은 시설 식별 검색을 그대로 수행하므로(out_of_scope_node 가
        # intent=VECTOR_SEARCH + vector_sub_intent=attribute_gap 로 식별 검색 경로에
        # 연결한다) identification 과 동일한 식별 가중치를 공유한다. 별도 프로파일이
        # 없으면 vector_sub_intent_enabled=True 전환 시 semantic 디폴트(track_a=0.15)로
        # 떨어져 식별 정확도가 저하되므로 alias 로 둔다.
        "attribute_gap": {
            "track_a": 0.5,
            "track_b": 0.25,
            "track_c": 0.25,
            "bm25": 0.5,
        },
        "detail": {"track_a": 0.2, "track_b": 0.5, "track_c": 0.3, "bm25": 0.4},
        "semantic": {"track_a": 0.15, "track_b": 0.35, "track_c": 0.5, "bm25": 0.3},
    }

    # baseline 모드: True → 모든 채널 가중치 1.0 (비가중치 RRF).
    # False 전환 조건: recall@k baseline 측정 완료 후 가중치 활성화.
    rrf_unweighted_baseline: bool = True


settings = Settings()
