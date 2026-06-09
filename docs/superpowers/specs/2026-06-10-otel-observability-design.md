# OpenTelemetry 기반 관측성(로깅·추적·메트릭) 설계

- **작성일**: 2026-06-10
- **대상**: on-seoul-api (Spring Boot), on-seoul-agent (FastAPI), docker-compose
- **상태**: 설계 확정, 구현 계획 작성 대기

## 1. 목표 / 요구사항

서울 공공서비스 예약 AI Agent 서비스의 두 컴포넌트(API 서비스·AI 서비스)에 OpenTelemetry 기반
**트레이스·메트릭·로그** 세 시그널을 도입한다.

| # | 요구사항 | 충족 방법 |
|---|----------|-----------|
| 1 | 통신방식 기본 gRPC, host를 `.env`로 설정 가능 | 표준 `OTEL_EXPORTER_OTLP_PROTOCOL=grpc`, `OTEL_EXPORTER_OTLP_ENDPOINT` env. 두 서비스가 동일 변수명 사용 |
| 2 | 알림·채팅에서 AI 서비스 호출 시 span 기록 | Java Agent의 WebClient 자동 client span + `@WithSpan` 비즈니스 span |
| 3 | 동일 OTEL 서버 사용 시 분산 트레이싱 | Java Agent가 `traceparent`(W3C) 자동 주입 → FastAPI instrumentor가 자동 추출 |

## 2. 현황 (조사 결과)

### AI 서비스 (on-seoul-agent, FastAPI) — OTEL 거의 완비
- `pyproject.toml`: `opentelemetry-sdk`, `exporter-otlp-proto-grpc`, instrumentation(fastapi/httpx/asyncpg/redis) 설치됨.
- `core/telemetry.py`: `setup_telemetry(app)` — fail-open 패턴, FastAPI·HTTPX·Redis·SQLAlchemy instrumentor 부착.
- `core/config.py`: `otel_enabled`(기본 False), `otel_service_name`, `otel_exporter_otlp_endpoint`(=`http://on-seoul-signoz:4317`), `otel_environment`, `otel_exporter_otlp_timeout`.
- `.env.example`: `OTEL_ENABLED`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, `OTEL_ENVIRONMENT` 정의.
- **미비**: OTLP **로그 export 미구성** (현재 logging은 stdout만). 분산 트레이싱 수신(traceparent 추출)은 fastapi instrumentor로 이미 가능.

### API 서비스 (on-seoul-api, Spring Boot 3.5.13, Gradle 멀티모듈) — OTEL 전무
- OpenTelemetry/Micrometer-tracing 의존성 **없음**. actuator는 일부 모듈만.
- AI 서비스 호출 3개 (reactive WebClient):
  - `chat/.../ChatAgentClient.java` → `POST /chat/stream` (SSE Flux 릴레이)
  - `notification/.../TemplateAgentClient.java` → `POST /notification/template`
  - `collection/.../EmbeddingSyncClient.java` → `POST /embeddings/services/sync`
- `bootstrap/.../application.yml`: env 패턴 `${VAR:default}`, `AI_SERVICE_URL` 등.
- `Dockerfile`: JRE 런타임 스테이지(빌드는 CI). 기존 `ENV JAVA_TOOL_OPTIONS="-Dnetworkaddress.cache.ttl=30"`, `ENTRYPOINT ["java","-jar","app.jar"]`. `curl`/`wget` 설치돼 있음.

### 인프라
- `docker-compose-app.yml`: `api`(49891:8080) + `agent`(49890:8000), 외부 네트워크 `on-seoul-net`.
- env_file 분리: `.env.deploy`(api·agent 공유) + `.env.api` / `.env.agent`(서비스별).
- 관측 백엔드(SigNoz/Collector)는 **별도 인프라에 이미 운영 중** — 두 서비스는 endpoint만 가리킨다.

## 3. 아키텍처

```
[프론트] → [API 서비스(Spring, :8080)] ──WebClient(traceparent 주입)──> [AI 서비스(FastAPI, :8000)]
                  │  -javaagent (OTel Java Agent)              │  OTel SDK (기구비)
                  │  traces / metrics / logs                   │  traces / metrics / logs
                  └────────────── gRPC :4317 ──────────────────┘
                                      │
                       [기존 운영 중 OTEL 백엔드 (SigNoz/Collector)]
```

- API 서비스가 AI 서비스를 호출하면 Java Agent가 client span 생성 + `traceparent` 주입.
- FastAPI instrumentor가 `traceparent`를 추출 → API span(부모) 아래 AI agent/tool span(자식)이 **단일 trace**로 연결.
- 두 서비스가 동일 endpoint(gRPC 4317)로 전송 → 백엔드에서 분산 트레이스 일관 조회.

## 4. 컴포넌트별 설계

### 4.1 API 서비스 — OTel Java Agent (자동 계측)

**계측 방식 결정: OpenTelemetry Java Agent**
- 근거: 요구#1(기본 gRPC + host `.env`, 세 시그널 통일)을 표준 OTEL env 하나로 충족하며, FastAPI SDK와 **동일 변수명**을 사용해 양 서비스 설정이 대칭. WebClient·JDBC·Redis·HTTP server 자동 계측 + W3C 전파 무코드.
- 대안(Micrometer Tracing)은 시그널별 설정 분리·메트릭 OTLP 기본 HTTP로 gRPC 통일에 손이 더 가므로 기각.

**Dockerfile 변경** (`on-seoul-api/Dockerfile`)
```dockerfile
ARG OTEL_AGENT_VERSION=2.11.0
RUN wget -O /app/opentelemetry-javaagent.jar \
    https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v${OTEL_AGENT_VERSION}/opentelemetry-javaagent.jar
COPY entrypoint.sh /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]
```
> 주의: 비루트 사용자(`jazz`)로 전환 **전에** jar 다운로드 및 `entrypoint.sh` 복사/권한 부여. agent jar와 스크립트는 root 소유 + 읽기/실행 권한이면 충분.

**entrypoint.sh** (신규, `on-seoul-api/entrypoint.sh`)
```sh
#!/bin/sh
set -e
JAVA_OPTS="-Dnetworkaddress.cache.ttl=30"        # 기존 JAVA_TOOL_OPTIONS 값 이관
if [ "${OTEL_ENABLED}" = "true" ]; then
  JAVA_OPTS="$JAVA_OPTS -javaagent:/app/opentelemetry-javaagent.jar"
fi
exec java $JAVA_OPTS -jar /app/app.jar
```
- 기존 `ENV JAVA_TOOL_OPTIONS="-Dnetworkaddress.cache.ttl=30"` 라인은 제거(스크립트로 이관)하거나, 유지하되 `-javaagent`만 조건부로 `JAVA_TOOL_OPTIONS`에 **append**. 둘 중 entrypoint 스크립트 방식 채택(토글 명확).
- `OTEL_ENABLED=false`(기본): agent 미부착 → 런타임 오버헤드 0, 정상 기동.

**AI 호출 비즈니스 span** (요구#2)
- 의존성: `io.opentelemetry.instrumentation:opentelemetry-instrumentation-annotations` (compile, 버전은 BOM 또는 agent와 호환되는 안정 버전 핀). 런타임 처리는 agent가 담당.
- 세 클라이언트 메서드에 `@WithSpan` + `@SpanAttribute` 부여:

| 클래스 | span 이름 | 속성(예) |
|--------|-----------|----------|
| `ChatAgentClient` | `ai.chat.stream` | `user.id`, `conversation.id` |
| `TemplateAgentClient` | `ai.notification.template` | `notification.type`, `user.id` |
| `EmbeddingSyncClient` | `ai.embedding.sync` | `service.count` |
- 자동 HTTP client span(agent)이 부모, 비즈니스 span은 의미 부여용. SSE(`ChatAgentClient`)는 reactive Flux이므로 span 컨텍스트가 구독 스레드와 분리되지 않도록 검증 필요(아래 §6 위험).

### 4.2 AI 서비스 — OTLP 로그 export 추가

- 기존 트레이스·메트릭은 `core/telemetry.py`로 이미 동작. **로그 export만 신규**.
- `opentelemetry-sdk`의 `LoggingHandler`(`opentelemetry.sdk._logs`)를 root logger에 부착하고 `OTLPLogExporter`(gRPC) + `BatchLogRecordProcessor` 구성. `core/telemetry.py`의 `setup_telemetry`에 fail-open으로 추가.
- 표준 env로 제어: `OTEL_LOGS_EXPORTER=otlp`(기본 미설정 시 export 안 함 → 안전). 기존 stdout 로깅은 유지.
- PII: 기존 정책(서드파티 logger quiet, 민감정보 WARN)을 OTLP 경로에도 동일 적용. 로그 export 활성 전 본문에 PII 없는지 재검토.

### 4.3 환경변수 (FastAPI와 대칭)

`.env.deploy` (api·agent **공유**) — 신규 추가:
```bash
OTEL_ENABLED=false
OTEL_EXPORTER_OTLP_ENDPOINT=http://on-seoul-signoz:4317   # 기본 gRPC, 호스트 미노출
OTEL_EXPORTER_OTLP_PROTOCOL=grpc                          # 요구#1: 기본 grpc
OTEL_ENVIRONMENT=prod                                     # local | prod
OTEL_LOGS_EXPORTER=otlp                                   # 로그 본문 OTLP 전송
```
`.env.api`: `OTEL_SERVICE_NAME=on-seoul-api`
`.env.agent`: `OTEL_SERVICE_NAME=on-seoul-agent`

> 주의: 두 서비스가 같은 `.env.deploy`를 공유하므로 `OTEL_SERVICE_NAME`은 반드시 서비스별 파일로 분리한다(같이 두면 한쪽 이름으로 덮임).
> `.env.example`의 기존 OTEL 블록도 위 변수(PROTOCOL, LOGS_EXPORTER 추가, 두 서비스 공유 의미)에 맞춰 갱신.

### 4.4 docker-compose 변경

- 구조 변경 최소. `api`/`agent` 서비스는 이미 `.env.deploy`+서비스별 env_file을 로드하므로 **변수 추가만으로 동작**.
- API 이미지 빌드 시 `OTEL_AGENT_VERSION` build arg가 필요하면 CI/compose `build.args`에 노출(현재 compose는 prebuilt image 사용 → 기본값으로 Dockerfile에 고정, 변경 시 CI에서 주입).
- 관측 백엔드는 이미 `on-seoul-net`에서 `on-seoul-signoz:4317`로 접근 가능(별도 운영). compose에 백엔드 서비스 추가 **불필요**.

## 5. 메트릭·로그 정책

- **메트릭**: Java Agent 자동(JVM, HTTP server/client). 기존 actuator/resilience4j(Micrometer) 메트릭과 중복 최소화 — 초기엔 agent 메트릭 위주, 필요 시 Micrometer→OTLP 브리지는 후속 검토(이번 범위 외).
- **로그**: 양 서비스 모두 OTLP로 본문 전송 + `trace_id`/`span_id` 상관. API는 agent의 logback 계측이 MDC 자동 주입, AI는 §4.2 핸들러.
- **샘플링**: 초기엔 100%(`parentbased_always_on`, agent 기본). 트래픽 증가 시 `OTEL_TRACES_SAMPLER`로 조정(후속).

## 6. 위험 / 검증

| 위험 | 대응 |
|------|------|
| SSE(reactive Flux) span 컨텍스트 누락/조기 종료 | `OTEL_ENABLED=true`로 `/chat/stream` 호출해 API client span이 스트림 종료까지 유지되고 FastAPI span과 연결되는지 백엔드에서 확인 |
| non-root 사용자에서 agent jar 권한 오류 | Dockerfile에서 root 단계에 다운로드, 읽기 권한 보장 |
| `JAVA_TOOL_OPTIONS` 덮어쓰기로 DNS TTL 옵션 유실 | entrypoint 스크립트로 기존 옵션 명시 이관 |
| 로그 OTLP에 PII 유출 | export 활성 전 본문 PII 재검토, 기존 quiet 정책 유지 |
| agent 버전과 annotations 의존성 비호환 | 버전 핀 + 로컬 기동 스모크 테스트 |

**검증 시나리오**
1. `OTEL_ENABLED=false`(기본) — 두 서비스 정상 기동, agent 미부착 확인.
2. `OTEL_ENABLED=true` — 로컬/스테이징 기동, `/chat/stream` 1회 호출.
3. 백엔드에서 API의 `ai.chat.stream` span → FastAPI agent/tool span이 **단일 `trace_id`** 로 연결되는지 확인.
4. 메트릭·로그가 endpoint로 수신되는지, 로그에 `trace_id` 상관 확인.

## 7. 롤아웃

1. 기본 `OTEL_ENABLED=false`로 머지 (무영향).
2. 스테이징에서 `true` 전환 → §6 검증 통과.
3. 프로덕션 적용.

## 8. 범위 밖 (YAGNI)

- LangChain/LLM 전용 계측(Langfuse 등) — 별도 페이즈.
- Micrometer→OTLP 메트릭 브리지(커스텀 비즈니스 메트릭) — 후속.
- 트레이스 샘플링 정책 정교화 — 트래픽 증가 시.
- 관측 백엔드(SigNoz) 자체 구성/대시보드 — 별도 인프라 소관(이미 운영 중).
