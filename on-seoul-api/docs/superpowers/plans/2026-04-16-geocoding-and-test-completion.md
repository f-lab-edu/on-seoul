# Geocoding Fallback & Test Completion (Phase 8-9)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 좌표 누락 레코드에 카카오 키워드 검색 API로 좌표를 보정(Phase 8)하고, `prev_service_status` 갱신 검증 테스트를 추가하여 Phase 9를 완료한다.

**Architecture:** `GeocodingService`는 `CollectionService.collectAll()` 완료 후 `coord_x IS NULL OR coord_y IS NULL`인 레코드를 일괄 조회하여 카카오 API로 좌표를 채운다. 인스턴스 레벨 `Map<String, Optional<BigDecimal[]>>` 캐시로 동일 장소명 중복 호출을 방지한다. `kakao.api.key`가 미설정(빈 문자열)이면 sweep을 스킵한다.

**Tech Stack:** Spring Boot 3.5, WebClient, Kakao Local API (`/v2/local/search/keyword.json`), MockWebServer (테스트), Mockito

---

## File Map

| 작업 | 파일 | 역할 |
|---|---|---|
| 수정 | `domain/.../domain/PublicServiceReservation.java` | `updateCoords()` 메서드 추가 |
| 수정 | `domain/.../repository/PublicServiceReservationRepository.java` | `findAllByCoordXIsNullOrCoordYIsNull()` 추가 |
| 생성 | `collector/.../config/KakaoApiProperties.java` | `kakao.api.*` 설정 바인딩 |
| 수정 | `collector/.../config/CollectorConfig.java` | `kakaoWebClient` Bean 추가 |
| 생성 | `collector/.../dto/KakaoGeocodingResponse.java` | 카카오 API 응답 DTO |
| 생성 | `collector/.../KakaoGeocodingClient.java` | 카카오 API 호출 클라이언트 |
| 생성 | `collector/.../service/GeocodingService.java` | 좌표 보정 sweep + 캐시 |
| 수정 | `collector/.../service/CollectionService.java` | collectAll() 끝에 geocoding sweep 호출 |
| 수정 | `app/.../OnSeoulApiApplicationTests.java` | `@MockitoBean GeocodingService` 추가 |
| 수정 | `collector/.../service/UpsertServiceTest.java` | `prev_service_status` 갱신 검증 테스트 추가 |
| 생성(테스트) | `collector/.../KakaoGeocodingClientTest.java` | MockWebServer 기반 클라이언트 테스트 |
| 생성(테스트) | `collector/.../service/GeocodingServiceTest.java` | sweep / 캐시 / key 미설정 테스트 |

---

## Task 1: Phase 9 — prev_service_status 갱신 테스트 추가

**Files:**
- Modify: `collector/src/test/java/dev/jazzybyte/onseoul/collector/service/UpsertServiceTest.java`

- [ ] **Step 1: 테스트 추가**

기존 `changed_entity_is_updated_with_change_log()` 테스트 다음에 아래 테스트를 추가한다.

```java
@Test
@DisplayName("UPDATE 시 prevServiceStatus에 변경 전 상태가 저장된다")
void prev_service_status_is_saved_on_update() {
    PublicServiceReservation existing = reservation("SVC001", "접수중",
            LocalDateTime.of(2026, 1, 1, 0, 0),
            LocalDateTime.of(2026, 3, 31, 23, 59));
    PublicServiceReservation incoming = reservation("SVC001", "안내중",
            LocalDateTime.of(2026, 1, 1, 0, 0),
            LocalDateTime.of(2026, 3, 31, 23, 59));
    when(reservationRepository.findAllByServiceIdIn(anyCollection())).thenReturn(List.of(existing));

    upsertService.upsert(List.of(incoming), 1L);

    assertThat(existing.getPrevServiceStatus()).isEqualTo("접수중");
    assertThat(existing.getServiceStatus()).isEqualTo("안내중");
}
```

- [ ] **Step 2: 테스트 통과 확인**

```bash
./gradlew :collector:test --tests "*.UpsertServiceTest" --console=plain 2>&1 | tail -5
```
Expected: BUILD SUCCESSFUL

- [ ] **Step 3: 커밋**

```bash
git add collector/src/test/java/dev/jazzybyte/onseoul/collector/service/UpsertServiceTest.java
git commit -m "test(collector): UpsertService prevServiceStatus 갱신 검증 테스트 추가"
```

---

## Task 2: PublicServiceReservation — updateCoords() + Repository 메서드 추가

**Files:**
- Modify: `domain/src/main/java/dev/jazzybyte/onseoul/domain/PublicServiceReservation.java`
- Modify: `domain/src/main/java/dev/jazzybyte/onseoul/repository/PublicServiceReservationRepository.java`

- [ ] **Step 1: `updateCoords()` 메서드를 entity에 추가**

`softDelete()` 메서드 아래에 추가:

```java
public void updateCoords(BigDecimal x, BigDecimal y) {
    this.coordX = x;
    this.coordY = y;
    this.lastSyncedAt = LocalDateTime.now();
}
```

- [ ] **Step 2: Repository에 `findAllByCoordXIsNullOrCoordYIsNull()` 추가**

```java
List<PublicServiceReservation> findAllByCoordXIsNullOrCoordYIsNull();
```

- [ ] **Step 3: 빌드 확인**

```bash
./gradlew :domain:compileJava --rerun-tasks --console=plain 2>&1 | tail -5
```
Expected: BUILD SUCCESSFUL

---

## Task 3: KakaoApiProperties + CollectorConfig 수정

**Files:**
- Create: `collector/src/main/java/dev/jazzybyte/onseoul/collector/config/KakaoApiProperties.java`
- Modify: `collector/src/main/java/dev/jazzybyte/onseoul/collector/config/CollectorConfig.java`

- [ ] **Step 1: KakaoApiProperties 생성**

```java
package dev.jazzybyte.onseoul.collector.config;

import lombok.Getter;
import lombok.Setter;
import org.springframework.boot.context.properties.ConfigurationProperties;

@Getter
@Setter
@ConfigurationProperties(prefix = "kakao.api")
public class KakaoApiProperties {

    /** 카카오 REST API 키. 미설정(빈 문자열)이면 Geocoding sweep을 스킵한다. */
    private String key = "";
    private String baseUrl = "https://dapi.kakao.com";
}
```

- [ ] **Step 2: CollectorConfig에 `kakaoWebClient` Bean 추가**

기존 `seoulWebClient` 빈 다음에 추가한다.

`@EnableConfigurationProperties` 어노테이션도 `KakaoApiProperties`를 포함하도록 수정:

```java
@Configuration
@EnableConfigurationProperties({SeoulApiProperties.class, KakaoApiProperties.class})
public class CollectorConfig {

    @Bean
    public WebClient seoulWebClient(SeoulApiProperties properties) {
        HttpClient httpClient = HttpClient.create()
                .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, properties.getConnectTimeoutMs())
                .responseTimeout(Duration.ofSeconds(properties.getResponseTimeoutSeconds()));

        return WebClient.builder()
                .baseUrl(properties.getBaseUrl())
                .clientConnector(new ReactorClientHttpConnector(httpClient))
                .build();
    }

    @Bean
    public WebClient kakaoWebClient(KakaoApiProperties properties) {
        return WebClient.builder()
                .baseUrl(properties.getBaseUrl())
                .defaultHeader("Authorization", "KakaoAK " + properties.getKey())
                .build();
    }
}
```

- [ ] **Step 3: 빌드 확인**

```bash
./gradlew :collector:compileJava --rerun-tasks --console=plain 2>&1 | tail -5
```
Expected: BUILD SUCCESSFUL

---

## Task 4: KakaoGeocodingResponse DTO + KakaoGeocodingClient 구현

**Files:**
- Create: `collector/src/main/java/dev/jazzybyte/onseoul/collector/dto/KakaoGeocodingResponse.java`
- Create: `collector/src/main/java/dev/jazzybyte/onseoul/collector/KakaoGeocodingClient.java`
- Create: `collector/src/test/java/dev/jazzybyte/onseoul/collector/KakaoGeocodingClientTest.java`

카카오 키워드 검색 API 응답 샘플:
```json
{
  "documents": [{"x": "126.9784", "y": "37.5665", "place_name": "서울시청"}],
  "meta": {"total_count": 1, "is_end": true}
}
```
`x` = 경도(longitude), `y` = 위도(latitude)

- [ ] **Step 1: 실패 테스트 작성**

```java
package dev.jazzybyte.onseoul.collector;

import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.collector.config.KakaoApiProperties;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.*;
import org.springframework.web.reactive.function.client.WebClient;

import java.io.IOException;
import java.math.BigDecimal;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

class KakaoGeocodingClientTest {

    static MockWebServer mockWebServer;
    KakaoGeocodingClient client;

    @BeforeAll
    static void startServer() throws IOException {
        mockWebServer = new MockWebServer();
        mockWebServer.start();
    }

    @AfterAll
    static void stopServer() throws IOException {
        mockWebServer.shutdown();
    }

    @BeforeEach
    void setUp() {
        KakaoApiProperties properties = new KakaoApiProperties();
        properties.setBaseUrl("http://localhost:" + mockWebServer.getPort());
        properties.setKey("test-api-key");

        WebClient kakaoWebClient = WebClient.builder()
                .baseUrl(properties.getBaseUrl())
                .defaultHeader("Authorization", "KakaoAK " + properties.getKey())
                .build();

        client = new KakaoGeocodingClient(kakaoWebClient, new ObjectMapper());
    }

    @Test
    @DisplayName("장소명으로 검색하면 좌표를 반환한다")
    void search_returns_coords_for_place() throws InterruptedException {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .addHeader("Content-Type", "application/json")
                .setBody("""
                        {"documents":[{"x":"126.9784","y":"37.5665","place_name":"서울시청"}],
                         "meta":{"total_count":1}}
                        """));

        Optional<BigDecimal[]> result = client.search("서울시청");

        assertThat(result).isPresent();
        assertThat(result.get()[0]).isEqualByComparingTo("126.9784");
        assertThat(result.get()[1]).isEqualByComparingTo("37.5665");

        RecordedRequest request = mockWebServer.takeRequest();
        assertThat(request.getPath()).contains("query=%EC%84%9C%EC%9A%B8%EC%8B%9C%EC%B2%AD"); // URL-encoded "서울시청"
        assertThat(request.getHeader("Authorization")).isEqualTo("KakaoAK test-api-key");
    }

    @Test
    @DisplayName("검색 결과가 없으면 Optional.empty()를 반환한다")
    void search_returns_empty_when_no_result() {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .addHeader("Content-Type", "application/json")
                .setBody("""
                        {"documents":[],"meta":{"total_count":0}}
                        """));

        Optional<BigDecimal[]> result = client.search("존재하지않는장소명");

        assertThat(result).isEmpty();
    }

    @Test
    @DisplayName("API 오류(4xx/5xx) 시 Optional.empty()를 반환한다")
    void search_returns_empty_on_api_error() {
        mockWebServer.enqueue(new MockResponse().setResponseCode(500));

        Optional<BigDecimal[]> result = client.search("서울시청");

        assertThat(result).isEmpty();
    }
}
```

- [ ] **Step 2: 테스트 FAIL 확인**

```bash
./gradlew :collector:test --tests "*.KakaoGeocodingClientTest" --console=plain 2>&1 | tail -5
```
Expected: FAIL (KakaoGeocodingClient 미구현)

- [ ] **Step 3: KakaoGeocodingResponse DTO 생성**

```java
package dev.jazzybyte.onseoul.collector.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.util.List;

@Getter
@NoArgsConstructor
public class KakaoGeocodingResponse {

    private List<Document> documents;

    @Getter
    @NoArgsConstructor
    public static class Document {
        /** 경도 (longitude) */
        private String x;
        /** 위도 (latitude) */
        private String y;
        @JsonProperty("place_name")
        private String placeName;
    }
}
```

- [ ] **Step 4: KakaoGeocodingClient 구현**

```java
package dev.jazzybyte.onseoul.collector;

import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import dev.jazzybyte.onseoul.collector.dto.KakaoGeocodingResponse;

import java.math.BigDecimal;
import java.util.List;
import java.util.Optional;

@Slf4j
@Component
public class KakaoGeocodingClient {

    private static final String KEYWORD_SEARCH_PATH = "/v2/local/search/keyword.json";

    private final WebClient kakaoWebClient;
    private final ObjectMapper objectMapper;

    public KakaoGeocodingClient(@Qualifier("kakaoWebClient") WebClient kakaoWebClient,
                                 ObjectMapper objectMapper) {
        this.kakaoWebClient = kakaoWebClient;
        this.objectMapper = objectMapper;
    }

    /**
     * 장소명으로 카카오 키워드 검색 API를 호출하여 좌표를 반환한다.
     *
     * @param placeName 장소명
     * @return [x(경도), y(위도)] 또는 결과 없음/오류 시 Optional.empty()
     */
    public Optional<BigDecimal[]> search(String placeName) {
        try {
            String body = kakaoWebClient.get()
                    .uri(uriBuilder -> uriBuilder
                            .path(KEYWORD_SEARCH_PATH)
                            .queryParam("query", placeName)
                            .queryParam("size", 1)
                            .build())
                    .retrieve()
                    .bodyToMono(String.class)
                    .block();

            if (body == null) {
                return Optional.empty();
            }

            KakaoGeocodingResponse response = objectMapper.readValue(body, KakaoGeocodingResponse.class);
            List<KakaoGeocodingResponse.Document> docs = response.getDocuments();

            if (docs == null || docs.isEmpty()) {
                return Optional.empty();
            }

            KakaoGeocodingResponse.Document doc = docs.get(0);
            return Optional.of(new BigDecimal[]{
                    new BigDecimal(doc.getX()),
                    new BigDecimal(doc.getY())
            });

        } catch (Exception e) {
            log.warn("카카오 Geocoding 실패 — placeName={}, error={}", placeName, e.getMessage());
            return Optional.empty();
        }
    }
}
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
./gradlew :collector:test --tests "*.KakaoGeocodingClientTest" --console=plain 2>&1 | tail -5
```
Expected: BUILD SUCCESSFUL

- [ ] **Step 6: 커밋**

```bash
git add collector/src/main/java/dev/jazzybyte/onseoul/collector/dto/KakaoGeocodingResponse.java \
        collector/src/main/java/dev/jazzybyte/onseoul/collector/KakaoGeocodingClient.java \
        collector/src/main/java/dev/jazzybyte/onseoul/collector/config/KakaoApiProperties.java \
        collector/src/main/java/dev/jazzybyte/onseoul/collector/config/CollectorConfig.java \
        collector/src/test/java/dev/jazzybyte/onseoul/collector/KakaoGeocodingClientTest.java \
        domain/src/main/java/dev/jazzybyte/onseoul/domain/PublicServiceReservation.java \
        domain/src/main/java/dev/jazzybyte/onseoul/repository/PublicServiceReservationRepository.java
git commit -m "feat(collector): KakaoGeocodingClient — 장소명 기반 카카오 API 좌표 조회"
```

---

## Task 5: GeocodingService 구현

**Files:**
- Create: `collector/src/main/java/dev/jazzybyte/onseoul/collector/service/GeocodingService.java`
- Create: `collector/src/test/java/dev/jazzybyte/onseoul/collector/service/GeocodingServiceTest.java`

- [ ] **Step 1: 실패 테스트 작성**

```java
package dev.jazzybyte.onseoul.collector.service;

import dev.jazzybyte.onseoul.collector.KakaoGeocodingClient;
import dev.jazzybyte.onseoul.collector.config.KakaoApiProperties;
import dev.jazzybyte.onseoul.domain.PublicServiceReservation;
import dev.jazzybyte.onseoul.repository.PublicServiceReservationRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class GeocodingServiceTest {

    @Mock private KakaoGeocodingClient geocodingClient;
    @Mock private PublicServiceReservationRepository repository;
    @Mock private KakaoApiProperties properties;

    @InjectMocks
    private GeocodingService geocodingService;

    @Test
    @DisplayName("API 키가 비어있으면 sweep을 스킵한다")
    void skips_when_api_key_is_blank() {
        when(properties.getKey()).thenReturn("");

        geocodingService.fillMissingCoords();

        verifyNoInteractions(repository, geocodingClient);
    }

    @Test
    @DisplayName("coordX/Y가 null인 레코드에 카카오 API로 좌표를 채운다")
    void fills_coords_for_null_coord_records() {
        when(properties.getKey()).thenReturn("valid-key");
        PublicServiceReservation record = PublicServiceReservation.builder()
                .serviceId("SVC001").serviceName("서울시청").serviceStatus("접수중")
                .build();
        // coordX, coordY는 null (builder에서 설정하지 않음)
        when(repository.findAllByCoordXIsNullOrCoordYIsNull()).thenReturn(List.of(record));
        when(geocodingClient.search("서울시청"))
                .thenReturn(Optional.of(new BigDecimal[]{new BigDecimal("126.9784"), new BigDecimal("37.5665")}));

        geocodingService.fillMissingCoords();

        verify(repository).save(record);
        assertThat(record.getCoordX()).isEqualByComparingTo("126.9784");
        assertThat(record.getCoordY()).isEqualByComparingTo("37.5665");
    }

    @Test
    @DisplayName("카카오 API 결과가 없는 장소명은 저장하지 않는다")
    void skips_record_when_geocoding_returns_empty() {
        when(properties.getKey()).thenReturn("valid-key");
        PublicServiceReservation record = PublicServiceReservation.builder()
                .serviceId("SVC001").serviceName("알수없는장소").serviceStatus("접수중")
                .build();
        when(repository.findAllByCoordXIsNullOrCoordYIsNull()).thenReturn(List.of(record));
        when(geocodingClient.search("알수없는장소")).thenReturn(Optional.empty());

        geocodingService.fillMissingCoords();

        verify(repository, never()).save(any());
    }

    @Test
    @DisplayName("동일 장소명은 API를 한 번만 호출한다 (캐시)")
    void caches_result_for_same_place_name() {
        when(properties.getKey()).thenReturn("valid-key");
        PublicServiceReservation r1 = PublicServiceReservation.builder()
                .serviceId("SVC001").serviceName("서울시청").serviceStatus("접수중").build();
        PublicServiceReservation r2 = PublicServiceReservation.builder()
                .serviceId("SVC002").serviceName("서울시청").serviceStatus("접수중").build();
        when(repository.findAllByCoordXIsNullOrCoordYIsNull()).thenReturn(List.of(r1, r2));
        when(geocodingClient.search("서울시청"))
                .thenReturn(Optional.of(new BigDecimal[]{new BigDecimal("126.9784"), new BigDecimal("37.5665")}));

        geocodingService.fillMissingCoords();

        verify(geocodingClient, times(1)).search("서울시청"); // 캐시로 인해 1회만 호출
        verify(repository, times(2)).save(any());
    }
}
```

- [ ] **Step 2: 테스트 FAIL 확인**

```bash
./gradlew :collector:test --tests "*.GeocodingServiceTest" --console=plain 2>&1 | tail -5
```
Expected: FAIL (GeocodingService 미구현)

- [ ] **Step 3: GeocodingService 구현**

```java
package dev.jazzybyte.onseoul.collector.service;

import dev.jazzybyte.onseoul.collector.KakaoGeocodingClient;
import dev.jazzybyte.onseoul.collector.config.KakaoApiProperties;
import dev.jazzybyte.onseoul.domain.PublicServiceReservation;
import dev.jazzybyte.onseoul.repository.PublicServiceReservationRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

@Slf4j
@Service
@RequiredArgsConstructor
public class GeocodingService {

    private final KakaoGeocodingClient geocodingClient;
    private final PublicServiceReservationRepository repository;
    private final KakaoApiProperties properties;

    /** 장소명 → 좌표 인스턴스 캐시 (동일 장소명 중복 API 호출 방지) */
    private final Map<String, Optional<BigDecimal[]>> coordsCache = new HashMap<>();

    /**
     * coordX 또는 coordY가 null인 레코드에 카카오 키워드 검색으로 좌표를 채운다.
     * {@code kakao.api.key}가 미설정이면 조용히 스킵한다.
     */
    public void fillMissingCoords() {
        if (properties.getKey().isBlank()) {
            log.warn("KAKAO_REST_API_KEY 미설정 — Geocoding sweep 스킵");
            return;
        }

        List<PublicServiceReservation> records = repository.findAllByCoordXIsNullOrCoordYIsNull();
        if (records.isEmpty()) {
            return;
        }

        log.info("Geocoding sweep 시작 — 대상 {}건", records.size());
        int filled = 0;

        for (PublicServiceReservation record : records) {
            String placeName = record.getPlaceName();
            if (placeName == null || placeName.isBlank()) {
                continue;
            }

            Optional<BigDecimal[]> coords = coordsCache.computeIfAbsent(placeName,
                    geocodingClient::search);

            if (coords.isPresent()) {
                record.updateCoords(coords.get()[0], coords.get()[1]);
                repository.save(record);
                filled++;
            }
        }

        log.info("Geocoding sweep 완료 — {}건 좌표 보정 (총 대상 {}건)", filled, records.size());
    }
}
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
./gradlew :collector:test --tests "*.GeocodingServiceTest" --console=plain 2>&1 | tail -5
```
Expected: BUILD SUCCESSFUL

- [ ] **Step 5: collector 전체 테스트 통과 확인**

```bash
./gradlew :collector:test --console=plain 2>&1 | tail -5
```
Expected: BUILD SUCCESSFUL

- [ ] **Step 6: 커밋**

```bash
git add collector/src/main/java/dev/jazzybyte/onseoul/collector/service/GeocodingService.java \
        collector/src/test/java/dev/jazzybyte/onseoul/collector/service/GeocodingServiceTest.java
git commit -m "feat(collector): GeocodingService — 좌표 누락 레코드 카카오 API 보정, 캐시 적용"
```

---

## Task 6: CollectionService에 Geocoding sweep 연결 + OnSeoulApiApplicationTests 수정

**Files:**
- Modify: `collector/src/main/java/dev/jazzybyte/onseoul/collector/service/CollectionService.java`
- Modify: `app/src/test/java/dev/jazzybyte/onseoul/OnSeoulApiApplicationTests.java`

- [ ] **Step 1: CollectionService에 GeocodingService 주입 + collectAll() 끝에 호출**

`CollectionService.java`의 필드와 `collectAll()` 메서드를 수정한다:

```java
// 필드 추가 (기존 필드들 아래에)
private final GeocodingService geocodingService;

// collectAll() 메서드에서 log.info("수집 완료"); 직전에 추가:
geocodingService.fillMissingCoords();
```

전체 `collectAll()` 메서드는 다음과 같다:

```java
public void collectAll() {
    List<ApiSourceCatalog> sources = catalogRepository.findAllByActiveTrue();
    if (sources.isEmpty()) {
        log.info("활성 소스 없음 — 수집 스킵");
        return;
    }

    log.info("수집 시작 — 대상 소스 {}개", sources.size());

    Set<String> allSeenServiceIds = new HashSet<>();
    boolean allSucceeded = true;

    for (ApiSourceCatalog source : sources) {
        boolean succeeded = collectOne(source, allSeenServiceIds);
        if (!succeeded) {
            allSucceeded = false;
        }
    }

    if (allSucceeded) {
        performDeletionSweep(allSeenServiceIds);
    } else {
        log.warn("일부 소스 수집 실패 — deletion sweep 건너뜀");
    }

    geocodingService.fillMissingCoords();

    log.info("수집 완료");
}
```

- [ ] **Step 2: CollectionServiceTest에 GeocodingService 목 추가**

`CollectionServiceTest.java`에 mock 필드와 lenient 스텁 추가:

```java
@Mock private GeocodingService geocodingService;
```

`@BeforeEach setUp()` 에 추가:
```java
lenient().doNothing().when(geocodingService).fillMissingCoords();
```

- [ ] **Step 3: OnSeoulApiApplicationTests에 GeocodingService 목 추가**

```java
import dev.jazzybyte.onseoul.collector.service.GeocodingService;

// 기존 @MockitoBean 아래에 추가:
@MockitoBean
GeocodingService geocodingService;
```

- [ ] **Step 4: 전체 테스트 통과 확인**

```bash
./gradlew :common:test :domain:test :collector:test :app:test --console=plain 2>&1 | tail -8
```
Expected: BUILD SUCCESSFUL

- [ ] **Step 5: 커밋**

```bash
git add collector/src/main/java/dev/jazzybyte/onseoul/collector/service/CollectionService.java \
        collector/src/test/java/dev/jazzybyte/onseoul/collector/service/CollectionServiceTest.java \
        app/src/test/java/dev/jazzybyte/onseoul/OnSeoulApiApplicationTests.java
git commit -m "feat(collector): CollectionService에 Geocoding sweep 연결"
```

---

## Task 7: docs 업데이트

**Files:**
- Modify: `on-seoul-api/docs/api-service-implementation.md`

- [ ] **Step 1: Phase 8, 9 체크박스 완료 처리**

Phase 8과 Phase 9의 모든 `- [ ]`를 `- [x]`로 변경한다.

- [ ] **Step 2: application.yml에 kakao.api.key 항목 추가 안내 (주석)**

`application.yml`은 deny 설정으로 Claude가 직접 수정할 수 없다. 아래 내용을 `application.yml`에 직접 추가한다:

```yaml
kakao:
  api:
    key: ${KAKAO_REST_API_KEY:}        # 미설정 시 Geocoding sweep 스킵
    base-url: https://dapi.kakao.com
```

- [ ] **Step 3: 커밋**

```bash
git add docs/api-service-implementation.md
git commit -m "docs: Phase 8-9 완료 처리"
```

---

## Self-Review

**Spec coverage:**
- [x] X/Y null 레코드에 대해 `PLACENM` 기반 카카오 Geocoding API 호출 → `KakaoGeocodingClient.search()` + `GeocodingService.fillMissingCoords()`
- [x] Upsert 후처리 → `CollectionService.collectAll()` 마지막에 `fillMissingCoords()` 호출
- [x] Geocoding 결과 캐싱 → `coordsCache` (HashMap in GeocodingService)
- [x] T4 prev_service_status 갱신 검증 → Task 1에서 추가

**Notes:**
- `GeocodingService.coordsCache`는 인스턴스 레벨 캐시로, 싱글턴 빈이므로 앱 재시작 전까지 유지된다. 동일 장소명은 재시작 전까지 Kakao API를 한 번만 호출한다.
- `kakao.api.key`가 미설정(`""`)이면 `GeocodingService.fillMissingCoords()`가 조용히 스킵하므로 개발 환경에서 key 없이도 앱이 정상 기동된다.
- `KakaoGeocodingClient`는 API 오류(4xx/5xx, 파싱 실패 등) 시 `Optional.empty()`를 반환하고 경고 로그만 남긴다 — 수집 파이프라인 전체가 중단되지 않는다.
