# OpenTelemetry 관측성(로깅·추적·메트릭) Implementation Plan

> **For agentic workers:** 이 프로젝트는 `on-seoul-orchestrator` 하네스로 실행한다. CLAUDE.md 지침에 따라 `superpowers:executing-plans` / `subagent-driven-development` 대신 named 에이전트(`infra`, `spring-backend`, `qa`, `code-reviewer`)로 라우팅한다. Steps는 체크박스(`- [ ]`)로 추적한다.

**Goal:** API 서비스(Spring Boot)에 OpenTelemetry Java Agent를 도입해 트레이스·메트릭·로그를 gRPC로 내보내고, 알림·채팅의 AI 서비스 호출을 span으로 기록하며, Spring→FastAPI 분산 트레이싱을 성립시킨다.

**Architecture:** API 서비스에 OTel Java Agent를 베이크하고 `OTEL_ENABLED` 토글로 부착 제어. WebClient 호출 시 agent가 W3C `traceparent`를 자동 주입 → FastAPI instrumentor가 추출해 단일 트레이스로 연결. AI 호출 3종에 `@WithSpan` 비즈니스 span + 채팅 SSE 수신 이벤트를 span event로 기록. Tomcat/Hikari/resilience4j 메트릭은 `micrometer-registry-otlp`로 export.

**Tech Stack:** OpenTelemetry Java Agent 2.x, opentelemetry-instrumentation-annotations, micrometer-registry-otlp, Spring Boot 3.5.13(Gradle 멀티모듈), Docker.

**구현 결정(설계서 §5.3 확정):** 트레이스·로그·JVM 메트릭은 Agent gRPC(:4317). Micrometer 프레임워크 메트릭(Tomcat·Hikari·resilience4j)은 결정론적 동작을 위해 `micrometer-registry-otlp`의 기본 **HTTP/protobuf(:4318)** 로 export한다(Agent의 Micrometer 전역레지스트리 브리지는 Spring 바인더를 확실히 잡지 못해 제외). 두 포트 모두 동일 SigNoz 호스트. SigNoz가 4318(HTTP)을 수신하는지 Task 7에서 확인한다.

**선행 확인:** 관측 백엔드(SigNoz/Collector)는 이미 `on-seoul-net`에서 운영 중이며 `on-seoul-signoz:4317`(gRPC)로 접근 가능하다고 가정한다. AI 서비스(FastAPI)는 트레이스·메트릭·로그가 이미 완비되어 있어 코드 변경이 없다(활성화·검증만).

---

## File Structure

| 파일 | 책임 | 작업 |
|------|------|------|
| `on-seoul-api/Dockerfile` | agent jar 베이크, entrypoint 교체 | Modify |
| `on-seoul-api/entrypoint.sh` | `OTEL_ENABLED` 토글로 `-javaagent` 조건부 부착 | Create |
| `.env.deploy` / `.env.api` / `.env.agent` | OTEL 공유·서비스별 env | Modify |
| `.env.example` | 마스터 템플릿 갱신 | Modify |
| `on-seoul-api/common/build.gradle` | `opentelemetry-instrumentation-annotations`(api 스코프) | Modify |
| `on-seoul-api/chat/.../ChatAgentClient.java` | `@WithSpan` + SSE 수신 span event | Modify |
| `on-seoul-api/notification/.../TemplateAgentClient.java` | `@WithSpan` | Modify |
| `on-seoul-api/collection/.../EmbeddingSyncClient.java` | `@WithSpan` | Modify |
| `on-seoul-api/chat/.../SseSpanEventRecorder.java` | SSE 이벤트 타입 추출 + span event 기록 헬퍼 | Create |
| `on-seoul-api/chat/.../SseSpanEventRecorderTest.java` | 헬퍼 단위 테스트 | Create |
| `on-seoul-api/bootstrap/build.gradle` | `micrometer-registry-otlp` | Modify |
| `on-seoul-api/bootstrap/src/main/resources/application.yml` | actuator 노출 + `management.otlp.metrics.export` | Modify |
| `docker-compose-app.yml` | (필요 시) build arg / 주석 | Modify |

---

## Task 1: entrypoint.sh — OTEL_ENABLED 토글

**담당:** infra

**Files:**
- Create: `on-seoul-api/entrypoint.sh`

- [ ] **Step 1: entrypoint.sh 작성**

`on-seoul-api/entrypoint.sh`:
```sh
#!/bin/sh
# OTEL_ENABLED=true 일 때만 OTel Java Agent를 부착한다.
# 기존 JAVA_TOOL_OPTIONS(-Dnetworkaddress.cache.ttl=30)는 여기로 이관해 유실을 막는다.
set -e

JAVA_OPTS="-Dnetworkaddress.cache.ttl=30"

if [ "${OTEL_ENABLED}" = "true" ]; then
  JAVA_OPTS="${JAVA_OPTS} -javaagent:/app/opentelemetry-javaagent.jar"
  echo "[entrypoint] OTel Java Agent 부착됨 (OTEL_ENABLED=true)"
else
  echo "[entrypoint] OTel 비활성 (OTEL_ENABLED=${OTEL_ENABLED:-unset})"
fi

exec java ${JAVA_OPTS} -jar /app/app.jar
```

- [ ] **Step 2: 실행권한 부여**

Run: `chmod +x on-seoul-api/entrypoint.sh && sh -n on-seoul-api/entrypoint.sh && echo OK`
Expected: `OK` (문법 오류 없음)

- [ ] **Step 3: 커밋**

```bash
git add on-seoul-api/entrypoint.sh
git commit -m "feat(otel): OTEL_ENABLED 토글 entrypoint 스크립트 추가"
```

---

## Task 2: Dockerfile — agent jar 베이크 + entrypoint 교체

**담당:** infra

**Files:**
- Modify: `on-seoul-api/Dockerfile`

- [ ] **Step 1: OTel agent 최신 안정 버전 확인**

Run: `curl -sI https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v2.11.0/opentelemetry-javaagent.jar | head -1`
Expected: `HTTP/2 302` (리다이렉트=존재). 404면 릴리스 페이지에서 최신 태그 확인 후 버전 교체.

- [ ] **Step 2: Dockerfile 수정**

기존 `ENV JAVA_TOOL_OPTIONS=...` 라인과 `ENTRYPOINT ["java","-jar","app.jar"]` 라인을 아래로 교체. agent jar 다운로드와 스크립트 복사는 **`USER jazz` 전환 전 root 단계**에서 수행한다.

`on-seoul-api/Dockerfile` — 비루트 전환(`USER jazz:jazz`) **이전**, 툴 설치 직후에 추가:
```dockerfile
# OTel Java Agent 베이크 (root 단계에서 다운로드, 읽기권한 보장)
ARG OTEL_AGENT_VERSION=2.11.0
RUN wget -O /app/opentelemetry-javaagent.jar \
      https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v${OTEL_AGENT_VERSION}/opentelemetry-javaagent.jar \
    && chmod 0644 /app/opentelemetry-javaagent.jar

# 토글 entrypoint
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod 0755 /app/entrypoint.sh
```

그리고 파일 하단에서 다음을 교체:
- 제거: `ENV JAVA_TOOL_OPTIONS="-Dnetworkaddress.cache.ttl=30"` (값은 entrypoint.sh로 이관됨)
- 교체: `ENTRYPOINT ["java", "-jar", "app.jar"]` → `ENTRYPOINT ["/app/entrypoint.sh"]`

> 주의: `WORKDIR /app` 가 root 단계에서 이미 설정되어 있으므로 `/app` 디렉터리는 root 소유. `COPY bootstrap/build/libs/bootstrap.jar app.jar`(기존)와 동일 위치. non-root(`jazz`)는 읽기/실행만 필요하므로 0644/0755로 충분.

- [ ] **Step 3: 빌드 컨텍스트 점검(런타임 스테이지 전용)**

Run: `grep -n "COPY\|ENTRYPOINT\|javaagent\|JAVA_TOOL_OPTIONS" on-seoul-api/Dockerfile`
Expected: `javaagent` 다운로드 라인, `COPY entrypoint.sh`, `ENTRYPOINT ["/app/entrypoint.sh"]` 존재. `JAVA_TOOL_OPTIONS` ENV 라인 없음.

- [ ] **Step 4: 커밋**

```bash
git add on-seoul-api/Dockerfile
git commit -m "feat(otel): Dockerfile에 OTel Java Agent 베이크 + 토글 entrypoint 적용"
```

---

## Task 3: 환경변수 — OTEL 공유/서비스별 분리

**담당:** infra

**Files:**
- Modify: `.env.deploy`, `.env.api`, `.env.agent`, `.env.example`

- [ ] **Step 1: 현재 OTEL 변수 위치 확인**

Run: `grep -rn "OTEL_" .env.deploy .env.api .env.agent .env.example 2>/dev/null`
Expected: 기존 `OTEL_*` 가 `.env.agent`/`.env.example`에 흩어져 있을 수 있음. 아래로 재배치.

- [ ] **Step 2: `.env.deploy`(api·agent 공유)에 추가/이동**

```bash
# ── OpenTelemetry (공유: api·agent 동일 백엔드) ──────────────
OTEL_ENABLED=false
OTEL_EXPORTER_OTLP_ENDPOINT=http://on-seoul-signoz:4317   # 트레이스/로그 gRPC
OTEL_EXPORTER_OTLP_PROTOCOL=grpc                          # 요구#1: 기본 grpc (Java Agent)
OTEL_ENVIRONMENT=prod
OTEL_LOGS_EXPORTER=otlp                                   # Java Agent 로그 export 제어
```

- [ ] **Step 3: 서비스별 service name 분리**

`.env.api` 에 추가: `OTEL_SERVICE_NAME=on-seoul-api`
`.env.agent` 에 추가(없으면): `OTEL_SERVICE_NAME=on-seoul-agent`
> 주의: 두 서비스가 같은 `.env.deploy`를 로드하므로 `OTEL_SERVICE_NAME`을 `.env.deploy`에 두면 안 된다(한쪽 이름으로 덮임). 반드시 서비스별 파일에만 둔다. `.env.deploy`에 기존 `OTEL_SERVICE_NAME`이 있으면 제거.

- [ ] **Step 4: `.env.example` 마스터 템플릿 갱신**

기존 OTEL 블록(주석에 "AI 서비스 → SigNoz")을 "api·agent 공유"로 갱신하고 `OTEL_EXPORTER_OTLP_PROTOCOL`, `OTEL_LOGS_EXPORTER`, 메트릭 HTTP 포트 주석 추가:
```bash
# ── OpenTelemetry (api·agent 공유, SigNoz) ───────────────────
OTEL_ENABLED=false                                       # 활성화: true
OTEL_EXPORTER_OTLP_ENDPOINT=http://on-seoul-signoz:4317  # 트레이스/로그 gRPC
OTEL_EXPORTER_OTLP_PROTOCOL=grpc                         # 기본 grpc
OTEL_ENVIRONMENT=prod                                    # local | prod
OTEL_LOGS_EXPORTER=otlp                                  # Java Agent 로그 export
# OTEL_SERVICE_NAME 은 .env.api / .env.agent 에서 서비스별로 지정
# Micrometer 프레임워크 메트릭은 http://on-seoul-signoz:4318 (HTTP/protobuf) 로 export (application.yml)
```

- [ ] **Step 5: 검증 + 커밋**

Run: `grep -n "OTEL_SERVICE_NAME" .env.deploy` → 결과 없어야 함(서비스별 파일로만 분리됨).
```bash
git add .env.example   # .env.deploy/.env.api/.env.agent 는 gitignore면 제외
git commit -m "feat(otel): OTEL env 공유/서비스별 분리 및 .env.example 갱신"
```
> 비고: `.env.deploy`/`.env.api`/`.env.agent`가 gitignore 대상이면 커밋되지 않으므로, 운영 호스트에서 동일 변경을 수동 반영하고 인수인계 메모를 남긴다.

---

## Task 4: OTel annotations 의존성 추가

**담당:** spring-backend

**Files:**
- Modify: `on-seoul-api/common/build.gradle`

- [ ] **Step 1: 의존성 추가**

`@WithSpan`/`@SpanAttribute`는 chat·notification·collection 모듈에서 쓰이며, 세 모듈 모두 `api project(':common')`로 common을 가져온다. common에 `api` 스코프로 한 번만 선언해 전파한다.

`on-seoul-api/common/build.gradle` 의 `dependencies { ... }` 에 추가:
```gradle
    // OTel @WithSpan/@SpanAttribute — 런타임 처리는 Java Agent가 담당(compile-time API만 필요)
    api 'io.opentelemetry.instrumentation:opentelemetry-instrumentation-annotations:2.11.0'
```
> 버전은 Task 2의 agent 버전과 동일 메이저(2.x)로 맞춘다. Spring Boot BOM이 관리하지 않으므로 명시 버전 필요.

- [ ] **Step 2: 컴파일 확인**

Run: `cd on-seoul-api && ./gradlew :common:compileJava -q && echo OK`
Expected: `OK` (의존성 해석·컴파일 성공)

- [ ] **Step 3: 커밋**

```bash
git add on-seoul-api/common/build.gradle
git commit -m "feat(otel): opentelemetry-instrumentation-annotations 의존성 추가(common)"
```

---

## Task 5: SSE 수신 이벤트 span event 기록 헬퍼 (TDD)

**담당:** spring-backend

**Files:**
- Create: `on-seoul-api/chat/src/main/java/dev/jazzybyte/onseoul/chat/adapter/out/agent/SseSpanEventRecorder.java`
- Test: `on-seoul-api/chat/src/test/java/dev/jazzybyte/onseoul/chat/adapter/out/agent/SseSpanEventRecorderTest.java`

이벤트 타입 분류는 span 기록과 분리해 순수 함수로 단위 테스트한다(reactive/agent 없이 검증 가능). 실제 span event 부착은 분류 결과를 받아 `Span.current().addEvent(...)`로 위임한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`SseSpanEventRecorderTest.java`:
```java
package dev.jazzybyte.onseoul.chat.adapter.out.agent;

import org.junit.jupiter.api.Test;
import org.springframework.http.codec.ServerSentEvent;

import static org.assertj.core.api.Assertions.assertThat;

class SseSpanEventRecorderTest {

    @Test
    void answer키가_있으면_final로_분류한다() {
        ServerSentEvent<String> sse = ServerSentEvent.<String>builder()
                .data("{\"answer\":\"안녕하세요\"}").build();
        assertThat(SseSpanEventRecorder.classify(sse)).isEqualTo("final");
    }

    @Test
    void event_decision이면_decision으로_분류한다() {
        ServerSentEvent<String> sse = ServerSentEvent.<String>builder()
                .data("{\"event\":\"decision\",\"action\":\"route\"}").build();
        assertThat(SseSpanEventRecorder.classify(sse)).isEqualTo("decision");
    }

    @Test
    void error키가_있으면_error로_분류한다() {
        ServerSentEvent<String> sse = ServerSentEvent.<String>builder()
                .data("{\"answer\":\"\",\"error\":\"boom\"}").build();
        assertThat(SseSpanEventRecorder.classify(sse)).isEqualTo("error");
    }

    @Test
    void data가_null이면_keepalive로_분류한다() {
        ServerSentEvent<String> sse = ServerSentEvent.<String>builder().data(null).build();
        assertThat(SseSpanEventRecorder.classify(sse)).isEqualTo("keepalive");
    }

    @Test
    void JSON이_아니면_relay로_분류한다() {
        ServerSentEvent<String> sse = ServerSentEvent.<String>builder().data("plain text").build();
        assertThat(SseSpanEventRecorder.classify(sse)).isEqualTo("relay");
    }

    @Test
    void answer_error_모두_없고_decision도_아니면_progress로_분류한다() {
        ServerSentEvent<String> sse = ServerSentEvent.<String>builder()
                .data("{\"event\":\"progress\",\"node\":\"router\"}").build();
        assertThat(SseSpanEventRecorder.classify(sse)).isEqualTo("progress");
    }
}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd on-seoul-api && ./gradlew :chat:test --tests "*SseSpanEventRecorderTest" -q`
Expected: FAIL — `SseSpanEventRecorder` 심볼 없음(컴파일 에러).

- [ ] **Step 3: 헬퍼 구현**

`SseSpanEventRecorder.java`:
```java
package dev.jazzybyte.onseoul.chat.adapter.out.agent;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.trace.Span;
import org.springframework.http.codec.ServerSentEvent;

/**
 * AI 서비스로부터 수신한 SSE 이벤트를 현재 span의 span event로 기록한다.
 *
 * <p>이벤트별 child span 대신 span event를 쓰는 이유: 토큰성 progress 이벤트가 많아
 * span 폭증(카디널리티)을 피하고, 하나의 스트림 span 타임라인에 수신 시점을 점으로 남긴다.
 */
final class SseSpanEventRecorder {

    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    private SseSpanEventRecorder() {
    }

    /** SSE data를 이벤트 타입 문자열로 분류한다(순수 함수, 테스트 대상). */
    static String classify(ServerSentEvent<String> sse) {
        String data = sse.data();
        if (data == null) {
            return "keepalive";
        }
        try {
            JsonNode node = OBJECT_MAPPER.readTree(data);
            if (!node.isObject()) {
                return "relay";
            }
            if (node.has("error")) {
                return "error";
            }
            if (node.has("answer")) {
                return "final";
            }
            JsonNode event = node.get("event");
            if (event != null && !event.isNull()) {
                return event.asText();   // "decision" / "progress" 등 AI가 명시한 타입
            }
            return "progress";
        } catch (Exception e) {
            return "relay";
        }
    }

    /**
     * 현재 활성 span에 수신 이벤트를 span event로 추가한다.
     * span 컨텍스트는 호출 측(doOnNext 클로저)에서 makeCurrent로 활성화되어 있어야 한다.
     * PII 보호: data 본문은 기록하지 않고 타입과 순번만 남긴다.
     */
    static void record(Span span, long seq, ServerSentEvent<String> sse) {
        if (span == null || !span.getSpanContext().isValid()) {
            return;
        }
        span.addEvent("sse.received", Attributes.builder()
                .put("sse.seq", seq)
                .put("sse.event_type", classify(sse))
                .build());
    }
}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd on-seoul-api && ./gradlew :chat:test --tests "*SseSpanEventRecorderTest" -q && echo OK`
Expected: `OK` (6개 테스트 PASS)

- [ ] **Step 5: 커밋**

```bash
git add on-seoul-api/chat/src/main/java/dev/jazzybyte/onseoul/chat/adapter/out/agent/SseSpanEventRecorder.java \
        on-seoul-api/chat/src/test/java/dev/jazzybyte/onseoul/chat/adapter/out/agent/SseSpanEventRecorderTest.java
git commit -m "feat(otel): SSE 수신 이벤트 분류·span event 기록 헬퍼 추가(TDD)"
```

---

## Task 6: ChatAgentClient — @WithSpan + SSE 수신 span event 연결

**담당:** spring-backend

**Files:**
- Modify: `on-seoul-api/chat/src/main/java/dev/jazzybyte/onseoul/chat/adapter/out/agent/ChatAgentClient.java`

- [ ] **Step 1: import 추가**

`ChatAgentClient.java` 상단 import 블록에 추가(실제 사용 심볼만):
```java
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.instrumentation.annotations.WithSpan;
import java.util.concurrent.atomic.AtomicLong;
```

- [ ] **Step 2: stream 메서드를 @WithSpan + SSE span event 버전으로 교체**

인터페이스 `AiServiceStreamPort.stream`의 시그니처는 **절대 바꾸지 않는다**. `@WithSpan("ai.chat.stream")`로 메서드 진입 span을 만들고, OTel agent의 reactor 계측이 Flux 구독 동안 그 span을 활성 컨텍스트로 전파한다. 수신 이벤트 기록은 span 참조를 클로저로 캡처해 스레드 전환과 무관하게 안전하게 처리한다.

기존 `stream(...)` 메서드 전체를 아래로 교체:
```java
    @Override
    @WithSpan("ai.chat.stream")
    public Flux<AiStreamEvent> stream(String question, long roomId, long messageId, Double lat, Double lng,
                                      List<ChatTurn> history, Carryover carryover) {
        // 진입 span(@WithSpan으로 생성됨)에 식별 속성 부여. PII 보호: question 평문은 넣지 않는다.
        Span span = Span.current();
        span.setAttribute("chat.room_id", roomId);
        span.setAttribute("chat.message_id", messageId);

        List<AiChatRequest.Turn> turns = (history == null ? List.<ChatTurn>of() : history).stream()
                .map(t -> new AiChatRequest.Turn(t.role(), t.content()))
                .toList();
        Carryover safeCarryover = carryover == null ? Carryover.empty() : carryover;
        List<AiChatRequest.PrevEntity> prevEntities = safeCarryover.prevEntities().stream()
                .map(e -> new AiChatRequest.PrevEntity(e.serviceId(), e.label()))
                .toList();
        span.setAttribute("chat.history_size", turns.size());
        AiChatRequest body = new AiChatRequest(roomId, messageId, question, lat, lng, turns,
                prevEntities, safeCarryover.prevIntent(), safeCarryover.prevReasoning());
        // PII 보호: 질문/대화 content 평문은 로깅하지 않고 식별자와 건수만 INFO로 남긴다.
        log.info("[Chat] 스트림 요청 to AI 서비스 - roomId={}, messageId={}, historySize={}, prevEntities={}, prevIntent={}",
                roomId, messageId, turns.size(), prevEntities.size(), safeCarryover.prevIntent());

        // 스트림 수신 이벤트를 기록할 span 참조를 클로저로 캡처(구독 스레드 전환 시 컨텍스트 유실 방지).
        final Span streamSpan = span;
        final AtomicLong seq = new AtomicLong(0);

        return webClient.post()
                .uri("/chat/stream")
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.TEXT_EVENT_STREAM)
                .bodyValue(body)
                .retrieve()
                .bodyToFlux(new ParameterizedTypeReference<ServerSentEvent<String>>() {})
                .timeout(Duration.ofSeconds(properties.streamTimeoutSeconds()))
                .doOnNext(sse -> SseSpanEventRecorder.record(streamSpan, seq.incrementAndGet(), sse))
                .mapNotNull(this::toStreamEvent)
                .doFinally(signal -> streamSpan.setAttribute("sse.event_count", seq.get()))
                .onErrorMap(TimeoutException.class,
                        e -> new OnSeoulApiException(ErrorCode.AI_SERVICE_TIMEOUT,
                                "AI 서비스 스트림 타임아웃: " + properties.streamTimeoutSeconds() + "초 초과", e))
                .onErrorMap(e -> !(e instanceof OnSeoulApiException),
                        e -> new OnSeoulApiException(ErrorCode.AI_SERVICE_ERROR,
                                "AI 서비스 스트림 오류: " + e.getMessage(), e));
    }
```
> import는 Step 1의 3개(`Span`, `WithSpan`, `AtomicLong`)만 사용한다. 미사용 import를 남기지 않는다.
> `@WithSpan`의 reactive 반환(`Flux`) 처리: OTel agent는 reactor를 계측하므로 `@WithSpan`이 만든 span이 Flux 구독 동안 활성 컨텍스트로 전파된다. `doOnNext`의 `streamSpan` 참조는 그 span을 직접 가리키므로 스레드 전환과 무관하게 안전하다.

- [ ] **Step 3: 컴파일 + 기존 테스트 회귀 확인**

Run: `cd on-seoul-api && ./gradlew :chat:compileJava :chat:test -q && echo OK`
Expected: `OK` — 컴파일 성공, 기존 chat 테스트 전부 통과(시그니처/동작 불변).

- [ ] **Step 4: 커밋**

```bash
git add on-seoul-api/chat/src/main/java/dev/jazzybyte/onseoul/chat/adapter/out/agent/ChatAgentClient.java
git commit -m "feat(otel): ChatAgentClient에 ai.chat.stream span + SSE 수신 span event 기록"
```

---

## Task 7: TemplateAgentClient·EmbeddingSyncClient — @WithSpan

**담당:** spring-backend

**Files:**
- Modify: `on-seoul-api/notification/.../TemplateAgentClient.java`
- Modify: `on-seoul-api/collection/.../EmbeddingSyncClient.java`

- [ ] **Step 1: TemplateAgentClient에 @WithSpan 추가**

import 추가:
```java
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.instrumentation.annotations.WithSpan;
```
`generate(...)` 메서드에 애너테이션과 속성 부여(try 진입 직후):
```java
    @Override
    @WithSpan("ai.notification.template")
    public TemplateResult generate(NotificationTemplateRequest request) {
        Span.current().setAttribute("notification.service_count", request.services().size());
        try {
            // ... 기존 본문 그대로 ...
```
> 본문(WebClient 호출·fallback)은 변경하지 않는다. 애너테이션 + 첫 줄 속성만 추가.

- [ ] **Step 2: EmbeddingSyncClient에 @WithSpan 추가**

import 추가:
```java
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.instrumentation.annotations.WithSpan;
```
`sync(...)` 메서드에 애너테이션과 속성 부여(빈배열 가드 이후, 호출 직전):
```java
    @Override
    @WithSpan("ai.embedding.sync")
    public void sync(List<String> upsert, List<String> delete) {
        if (upsert.isEmpty() && delete.isEmpty()) {
            log.debug("[EmbeddingSync] upsert/delete 모두 비어 있음 — AI 호출 생략");
            return;
        }
        Span.current().setAttribute("embedding.upsert_count", upsert.size());
        Span.current().setAttribute("embedding.delete_count", delete.size());
        // ... 기존 본문 그대로 ...
```

- [ ] **Step 3: 컴파일 + 회귀 테스트**

Run: `cd on-seoul-api && ./gradlew :notification:test :collection:test -q && echo OK`
Expected: `OK` — 두 모듈 테스트 통과(동작 불변).

- [ ] **Step 4: 커밋**

```bash
git add on-seoul-api/notification/src/main/java/dev/jazzybyte/onseoul/notification/adapter/out/agent/TemplateAgentClient.java \
        on-seoul-api/collection/src/main/java/dev/jazzybyte/onseoul/collection/adapter/out/agent/EmbeddingSyncClient.java
git commit -m "feat(otel): 알림 템플릿·임베딩 동기화 AI 호출에 @WithSpan 추가"
```

---

## Task 8: Micrometer 프레임워크 메트릭 → OTLP

**담당:** spring-backend / infra

**Files:**
- Modify: `on-seoul-api/bootstrap/build.gradle`
- Modify: `on-seoul-api/bootstrap/src/main/resources/application.yml`

- [ ] **Step 1: micrometer-registry-otlp 의존성 추가**

`on-seoul-api/bootstrap/build.gradle` 의 `dependencies { ... }` 에 추가(BOM 관리, 버전 생략):
```gradle
    implementation 'io.micrometer:micrometer-registry-otlp'
    // Tomcat 스레드풀·HikariCP·resilience4j 메트릭 바인더 노출용
    implementation 'org.springframework.boot:spring-boot-starter-actuator'
```
> 일부 모듈(notification/collection)에 이미 actuator가 있으나, 메트릭 자동설정은 실행 모듈(bootstrap)에 있어야 전역 적용된다.

- [ ] **Step 2: application.yml에 OTLP 메트릭 export 설정**

`management:` 블록을 다음으로 확장(기존 `endpoints`/`endpoint.health` 유지):
```yaml
management:
  endpoints:
    web:
      exposure:
        include: health
  endpoint:
    health:
      show-details: never
  otlp:
    metrics:
      export:
        # Micrometer OTLP 레지스트리는 기본 HTTP/protobuf. SigNoz의 HTTP 수신 포트(4318)/v1/metrics.
        # 트레이스·로그는 Java Agent가 gRPC(4317)로 별도 전송한다.
        enabled: ${OTEL_ENABLED:false}
        url: ${OTEL_METRICS_OTLP_URL:http://on-seoul-signoz:4318/v1/metrics}
        step: 60s
  metrics:
    tags:
      service.name: ${OTEL_SERVICE_NAME:on-seoul-api}
      deployment.environment: ${OTEL_ENVIRONMENT:local}
```

- [ ] **Step 3: `.env.deploy`에 메트릭 URL(선택) 추가**

`.env.example` 및 `.env.deploy`에 주석과 함께 추가:
```bash
# Micrometer 프레임워크 메트릭 (HTTP/protobuf, SigNoz 4318)
OTEL_METRICS_OTLP_URL=http://on-seoul-signoz:4318/v1/metrics
```

- [ ] **Step 4: 부팅 자동설정 검증**

Run: `cd on-seoul-api && ./gradlew :bootstrap:compileJava -q && echo OK`
Expected: `OK`. (런타임 검증은 Task 9 통합 검증에서 수행)

- [ ] **Step 5: 커밋**

```bash
git add on-seoul-api/bootstrap/build.gradle on-seoul-api/bootstrap/src/main/resources/application.yml .env.example
git commit -m "feat(otel): Micrometer 프레임워크 메트릭 OTLP(HTTP/4318) export 설정"
```

---

## Task 9: 통합 검증 — 분산 트레이싱·메트릭·로그

**담당:** qa

**Files:** (검증 전용, 코드 변경 없음)

- [ ] **Step 1: 전체 빌드**

Run: `cd on-seoul-api && ./gradlew clean build -q && echo BUILD_OK`
Expected: `BUILD_OK` — 전 모듈 컴파일·테스트 통과.

- [ ] **Step 2: OTEL_ENABLED=false 무영향 확인**

로컬 또는 스테이징에서 `OTEL_ENABLED=false`로 두 서비스 기동.
Run(컨테이너 로그): `docker logs on-seoul-api 2>&1 | grep entrypoint`
Expected: `[entrypoint] OTel 비활성` 출력, 앱 정상 기동(`/actuator/health` 200 또는 기존 헬스 경로).

- [ ] **Step 3: OTEL_ENABLED=true 활성화 + 채팅 1회 호출**

`OTEL_ENABLED=true`로 양 서비스 재기동 후 `/chat/stream` 1회 호출(프론트 또는 curl SSE).
Run: `docker logs on-seoul-api 2>&1 | grep "Agent 부착"`
Expected: `[entrypoint] OTel Java Agent 부착됨` 출력.

- [ ] **Step 4: 분산 트레이스 연결 확인(백엔드)**

SigNoz에서 최근 트레이스 조회.
Expected: `on-seoul-api`의 `ai.chat.stream` span을 부모로, `on-seoul-agent`의 FastAPI/agent/tool span이 **동일 `trace_id`** 아래 자식으로 연결. `ai.chat.stream` span에 `sse.received` 이벤트들과 `sse.event_count` 속성 존재.

- [ ] **Step 5: 알림·임베딩 경로 트레이스 확인**

알림 템플릿 생성 또는 임베딩 동기화 트리거.
Expected: `ai.notification.template` / `ai.embedding.sync` span이 FastAPI span과 단일 trace로 연결.

- [ ] **Step 6: 메트릭·로그 수신 확인**

Expected: SigNoz에서 (a) JVM/HTTP 메트릭(Agent, gRPC), (b) Tomcat 스레드풀·HikariCP·resilience4j 메트릭(Micrometer, HTTP 4318) 수신. **4318 미수신 시** SigNoz의 HTTP OTLP 수신 활성화 여부 확인(인프라) 후 재시도. 로그에 `trace_id`/`span_id` 상관 포함.

- [ ] **Step 7: 검증 결과 기록**

검증 통과/실패와 스크린샷·trace_id 샘플을 PR 설명 또는 `docs/`에 기록.

---

## Task 10: docker-compose 정합성 + 문서

**담당:** infra

**Files:**
- Modify: `docker-compose-app.yml` (필요 시)

- [ ] **Step 1: build arg 노출 여부 판단**

compose가 prebuilt 이미지를 쓰므로 `OTEL_AGENT_VERSION`은 Dockerfile 기본값으로 충분. CI에서 버전을 주입한다면 워크플로 `docker build --build-arg OTEL_AGENT_VERSION=...` 에 추가하고, compose에는 변경 불필요.

- [ ] **Step 2: compose 주석 보강**

`docker-compose-app.yml` 상단 또는 `api` 서비스에 OTEL 관련 주석 추가(선택):
```yaml
    # OTEL_ENABLED/OTEL_EXPORTER_OTLP_ENDPOINT 등은 .env.deploy(공유) + .env.api(service name)에서 주입.
    # 트레이스·로그 gRPC :4317, Micrometer 메트릭 HTTP :4318 (on-seoul-signoz).
```

- [ ] **Step 3: 커밋**

```bash
git add docker-compose-app.yml
git commit -m "docs(otel): docker-compose OTEL 구성 주석 보강"
```

---

## Self-Review 결과

- **요구#1(기본 grpc, host .env):** Task 3(env 분리, `OTEL_EXPORTER_OTLP_PROTOCOL=grpc`) + Task 8(메트릭만 HTTP 4318, 문서화된 예외). ✅
- **요구#2(알림·채팅 AI 호출 span):** Task 6(`ai.chat.stream` + SSE span event), Task 7(`ai.notification.template`/`ai.embedding.sync`). ✅
- **요구#3(분산 트레이싱):** Task 2(agent → traceparent 자동 주입) + Task 9 Step 4·5 검증. FastAPI는 기존 instrumentor로 추출. ✅
- **로그:** Agent logback 계측(API) + FastAPI 기존 `_setup_logs`. Task 9 Step 6 검증. ✅
- **메트릭:** Agent JVM/HTTP(Task 2) + Micrometer 프레임워크(Task 8). ✅
- **타입 일관성:** `SseSpanEventRecorder.classify/record`(Task 5) → Task 6에서 `record(streamSpan, seq, sse)`로 동일 시그니처 사용. ✅
- **플레이스홀더:** Task 6 Step 2는 "주의 환기용"임을 명시하고 Step 2-fix에 실제 코드 제공. 그 외 모든 step에 실제 코드/명령 포함. ✅
