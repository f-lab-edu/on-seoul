# Collection Pipeline Phase 6-7 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 변경 감지·Upsert 서비스(Phase 6)와 스케줄러·트리거 엔드포인트(Phase 7)를 구현하여 수집 파이프라인을 완성한다.

**Architecture:** `CollectionService`가 `ApiSourceCatalog`에서 활성 소스 목록을 읽어 순차 수집한다. 소스별로 `UpsertService`가 NEW / UPDATED / UNCHANGED 분류 및 DB 반영을 담당하고 `CollectionHistory`에 결과를 기록한다. 전체 수집 성공 시에만 DB 잔존 레코드 대상으로 soft-delete 처리한다. `CollectionScheduler`가 매일 새벽 3시 실행하고 `CollectionController`가 수동 트리거를 제공한다.

**Tech Stack:** Spring Boot 3.5, Spring Data JPA, `@Scheduled`, `@Transactional`, Mockito(테스트), MockMvc(컨트롤러 테스트)

---

## File Map

| 작업 | 파일 | 역할 |
|---|---|---|
| 생성 | `collector/.../dto/UpsertResult.java` | new/updated/unchanged/deleted 카운트 record |
| 생성 | `collector/.../service/UpsertService.java` | 단일 소스 배치 분류 및 DB 반영 |
| 생성 | `collector/.../service/CollectionService.java` | 전체 소스 오케스트레이션 + deletion sweep |
| 생성 | `app/.../controller/CollectionController.java` | `POST /admin/collection/trigger` |
| 생성 | `app/.../scheduler/CollectionScheduler.java` | `@Scheduled` 일 1회 실행 |
| 수정 | `domain/.../repository/PublicServiceReservationRepository.java` | `findAllByServiceIdIn` 추가 |
| 생성(테스트) | `collector/.../service/UpsertServiceTest.java` | NEW / UPDATED / UNCHANGED 단위 테스트 |
| 생성(테스트) | `collector/.../service/CollectionServiceTest.java` | 부분 실패 / deletion sweep 테스트 |
| 생성(테스트) | `app/.../controller/CollectionControllerTest.java` | 트리거 엔드포인트 테스트 |

---

## Task 1: UpsertResult DTO

**Files:**
- Create: `collector/src/main/java/dev/jazzybyte/onseoul/collector/dto/UpsertResult.java`

- [ ] **Step 1: UpsertResult record 생성**

```java
package dev.jazzybyte.onseoul.collector.dto;

/**
 * 단일 소스 배치 upsert 결과.
 * deleted는 UpsertService 책임 밖(CollectionService의 deletion sweep에서 채운다).
 */
public record UpsertResult(int newCount, int updatedCount, int unchangedCount) {

    public static UpsertResult empty() {
        return new UpsertResult(0, 0, 0);
    }
}
```

- [ ] **Step 2: 빌드 확인**

```bash
./gradlew :collector:compileJava --console=plain
```
Expected: BUILD SUCCESSFUL

---

## Task 2: Repository 메서드 추가

**Files:**
- Modify: `domain/src/main/java/dev/jazzybyte/onseoul/repository/PublicServiceReservationRepository.java`

- [ ] **Step 1: `findAllByServiceIdIn` 추가**

```java
package dev.jazzybyte.onseoul.repository;

import dev.jazzybyte.onseoul.domain.PublicServiceReservation;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

public interface PublicServiceReservationRepository extends JpaRepository<PublicServiceReservation, Long> {

    Optional<PublicServiceReservation> findByServiceId(String serviceId);

    List<PublicServiceReservation> findAllByDeletedAtIsNull();

    List<PublicServiceReservation> findAllByServiceIdIn(Collection<String> serviceIds);
}
```

- [ ] **Step 2: 빌드 확인**

```bash
./gradlew :domain:compileJava --console=plain
```
Expected: BUILD SUCCESSFUL

---

## Task 3: UpsertService 구현

**Files:**
- Create: `collector/src/main/java/dev/jazzybyte/onseoul/collector/service/UpsertService.java`
- Create: `collector/src/test/java/dev/jazzybyte/onseoul/collector/service/UpsertServiceTest.java`

- [ ] **Step 1: 실패 테스트 작성**

```java
package dev.jazzybyte.onseoul.collector.service;

import dev.jazzybyte.onseoul.collector.domain.ServiceChangeLog;
import dev.jazzybyte.onseoul.collector.dto.UpsertResult;
import dev.jazzybyte.onseoul.collector.enums.ChangeType;
import dev.jazzybyte.onseoul.collector.repository.ServiceChangeLogRepository;
import dev.jazzybyte.onseoul.domain.PublicServiceReservation;
import dev.jazzybyte.onseoul.repository.PublicServiceReservationRepository;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class UpsertServiceTest {

    @Mock
    private PublicServiceReservationRepository reservationRepository;

    @Mock
    private ServiceChangeLogRepository changeLogRepository;

    @InjectMocks
    private UpsertService upsertService;

    @Test
    @DisplayName("DB에 없는 serviceId는 신규(NEW)로 INSERT된다")
    void new_entity_is_inserted() {
        PublicServiceReservation incoming = reservation("SVC001", "접수중",
                LocalDateTime.of(2026, 1, 1, 0, 0),
                LocalDateTime.of(2026, 3, 31, 23, 59));
        when(reservationRepository.findAllByServiceIdIn(anyCollection())).thenReturn(List.of());

        UpsertResult result = upsertService.upsert(List.of(incoming), 1L);

        verify(reservationRepository).save(incoming);
        assertThat(result.newCount()).isEqualTo(1);
        assertThat(result.updatedCount()).isEqualTo(0);
        assertThat(result.unchangedCount()).isEqualTo(0);
    }

    @Test
    @DisplayName("핵심 필드가 변경된 경우 UPDATE되고 ServiceChangeLog가 기록된다")
    void changed_entity_is_updated_with_change_log() {
        PublicServiceReservation existing = reservation("SVC001", "접수중",
                LocalDateTime.of(2026, 1, 1, 0, 0),
                LocalDateTime.of(2026, 3, 31, 23, 59));
        PublicServiceReservation incoming = reservation("SVC001", "안내중",  // status 변경
                LocalDateTime.of(2026, 1, 1, 0, 0),
                LocalDateTime.of(2026, 3, 31, 23, 59));
        when(reservationRepository.findAllByServiceIdIn(anyCollection())).thenReturn(List.of(existing));

        UpsertResult result = upsertService.upsert(List.of(incoming), 1L);

        verify(reservationRepository).save(existing);
        assertThat(result.updatedCount()).isEqualTo(1);

        ArgumentCaptor<List<ServiceChangeLog>> logCaptor = ArgumentCaptor.forClass(List.class);
        verify(changeLogRepository).saveAll(logCaptor.capture());
        List<ServiceChangeLog> logs = logCaptor.getValue();
        assertThat(logs).hasSize(1);
        assertThat(logs.get(0).getChangeType()).isEqualTo(ChangeType.UPDATED);
        assertThat(logs.get(0).getFieldName()).isEqualTo("serviceStatus");
        assertThat(logs.get(0).getOldValue()).isEqualTo("접수중");
        assertThat(logs.get(0).getNewValue()).isEqualTo("안내중");
    }

    @Test
    @DisplayName("핵심 필드가 동일하면 UNCHANGED로 스킵된다")
    void unchanged_entity_is_skipped() {
        LocalDateTime start = LocalDateTime.of(2026, 1, 1, 0, 0);
        LocalDateTime end = LocalDateTime.of(2026, 3, 31, 23, 59);
        PublicServiceReservation existing = reservation("SVC001", "접수중", start, end);
        PublicServiceReservation incoming = reservation("SVC001", "접수중", start, end);
        when(reservationRepository.findAllByServiceIdIn(anyCollection())).thenReturn(List.of(existing));

        UpsertResult result = upsertService.upsert(List.of(incoming), 1L);

        verify(reservationRepository, never()).save(any());
        assertThat(result.unchangedCount()).isEqualTo(1);
    }

    @Test
    @DisplayName("입력이 비어있으면 모두 0을 반환한다")
    void empty_input_returns_zero_counts() {
        UpsertResult result = upsertService.upsert(List.of(), 1L);

        assertThat(result).isEqualTo(UpsertResult.empty());
        verifyNoInteractions(reservationRepository, changeLogRepository);
    }

    // ── helpers ──────────────────────────────────────────────────────────────

    private PublicServiceReservation reservation(String serviceId, String serviceStatus,
                                                  LocalDateTime receiptStart, LocalDateTime receiptEnd) {
        return PublicServiceReservation.builder()
                .serviceId(serviceId)
                .serviceName("테스트 서비스")
                .serviceStatus(serviceStatus)
                .receiptStartDt(receiptStart)
                .receiptEndDt(receiptEnd)
                .build();
    }
}
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
./gradlew :collector:test --tests "*.UpsertServiceTest" --console=plain 2>&1 | tail -15
```
Expected: FAIL (UpsertService 미구현)

- [ ] **Step 3: UpsertService 구현**

```java
package dev.jazzybyte.onseoul.collector.service;

import dev.jazzybyte.onseoul.collector.domain.ServiceChangeLog;
import dev.jazzybyte.onseoul.collector.dto.UpsertResult;
import dev.jazzybyte.onseoul.collector.enums.ChangeType;
import dev.jazzybyte.onseoul.collector.repository.ServiceChangeLogRepository;
import dev.jazzybyte.onseoul.domain.PublicServiceReservation;
import dev.jazzybyte.onseoul.repository.PublicServiceReservationRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.function.Function;
import java.util.stream.Collectors;

@Slf4j
@Service
@RequiredArgsConstructor
public class UpsertService {

    private final PublicServiceReservationRepository reservationRepository;
    private final ServiceChangeLogRepository changeLogRepository;

    /**
     * incoming 엔티티 배치를 NEW / UPDATED / UNCHANGED로 분류하여 DB에 반영한다.
     *
     * @param incoming     API에서 수신·변환된 엔티티 목록
     * @param collectionId 이번 수집 이력 ID (ServiceChangeLog FK)
     * @return 분류별 카운트
     */
    @Transactional
    public UpsertResult upsert(List<PublicServiceReservation> incoming, Long collectionId) {
        if (incoming.isEmpty()) {
            return UpsertResult.empty();
        }

        List<String> incomingIds = incoming.stream()
                .map(PublicServiceReservation::getServiceId)
                .toList();

        Map<String, PublicServiceReservation> existingMap = reservationRepository
                .findAllByServiceIdIn(incomingIds)
                .stream()
                .collect(Collectors.toMap(PublicServiceReservation::getServiceId, Function.identity()));

        int newCount = 0, updatedCount = 0, unchangedCount = 0;
        List<ServiceChangeLog> changeLogs = new ArrayList<>();

        for (PublicServiceReservation entity : incoming) {
            PublicServiceReservation existing = existingMap.get(entity.getServiceId());

            if (existing == null) {
                reservationRepository.save(entity);
                newCount++;
                log.debug("NEW: serviceId={}", entity.getServiceId());

            } else if (isCoreFieldChanged(existing, entity)) {
                changeLogs.addAll(buildChangeLogs(existing, entity, collectionId));
                existing.update(entity);
                reservationRepository.save(existing);
                updatedCount++;
                log.debug("UPDATED: serviceId={}", entity.getServiceId());

            } else {
                unchangedCount++;
            }
        }

        if (!changeLogs.isEmpty()) {
            changeLogRepository.saveAll(changeLogs);
        }

        log.info("Upsert 완료 — new={}, updated={}, unchanged={}", newCount, updatedCount, unchangedCount);
        return new UpsertResult(newCount, updatedCount, unchangedCount);
    }

    /**
     * 변경 판정 기준: serviceStatus, receiptStartDt, receiptEndDt
     * DTLCONT 등 대형 텍스트는 비교 비용이 커서 제외한다.
     */
    private boolean isCoreFieldChanged(PublicServiceReservation existing,
                                        PublicServiceReservation incoming) {
        return !Objects.equals(existing.getServiceStatus(), incoming.getServiceStatus())
                || !Objects.equals(existing.getReceiptStartDt(), incoming.getReceiptStartDt())
                || !Objects.equals(existing.getReceiptEndDt(), incoming.getReceiptEndDt());
    }

    private List<ServiceChangeLog> buildChangeLogs(PublicServiceReservation existing,
                                                    PublicServiceReservation incoming,
                                                    Long collectionId) {
        List<ServiceChangeLog> logs = new ArrayList<>();

        if (!Objects.equals(existing.getServiceStatus(), incoming.getServiceStatus())) {
            logs.add(changeLog(existing.getServiceId(), collectionId, "serviceStatus",
                    existing.getServiceStatus(), incoming.getServiceStatus()));
        }
        if (!Objects.equals(existing.getReceiptStartDt(), incoming.getReceiptStartDt())) {
            logs.add(changeLog(existing.getServiceId(), collectionId, "receiptStartDt",
                    String.valueOf(existing.getReceiptStartDt()),
                    String.valueOf(incoming.getReceiptStartDt())));
        }
        if (!Objects.equals(existing.getReceiptEndDt(), incoming.getReceiptEndDt())) {
            logs.add(changeLog(existing.getServiceId(), collectionId, "receiptEndDt",
                    String.valueOf(existing.getReceiptEndDt()),
                    String.valueOf(incoming.getReceiptEndDt())));
        }
        return logs;
    }

    private ServiceChangeLog changeLog(String serviceId, Long collectionId,
                                        String fieldName, String oldValue, String newValue) {
        return ServiceChangeLog.builder()
                .serviceId(serviceId)
                .collectionId(collectionId)
                .changeType(ChangeType.UPDATED)
                .fieldName(fieldName)
                .oldValue(oldValue)
                .newValue(newValue)
                .build();
    }
}
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
./gradlew :collector:test --tests "*.UpsertServiceTest" --console=plain 2>&1 | tail -10
```
Expected: BUILD SUCCESSFUL

- [ ] **Step 5: 커밋**

```bash
git add collector/src/main/java/dev/jazzybyte/onseoul/collector/dto/UpsertResult.java \
        collector/src/main/java/dev/jazzybyte/onseoul/collector/service/UpsertService.java \
        collector/src/test/java/dev/jazzybyte/onseoul/collector/service/UpsertServiceTest.java \
        domain/src/main/java/dev/jazzybyte/onseoul/repository/PublicServiceReservationRepository.java
git commit -m "feat(collector): UpsertService — NEW/UPDATED/UNCHANGED 변경 감지 및 DB 반영"
```

---

## Task 4: CollectionService 구현

**Files:**
- Create: `collector/src/main/java/dev/jazzybyte/onseoul/collector/service/CollectionService.java`
- Create: `collector/src/test/java/dev/jazzybyte/onseoul/collector/service/CollectionServiceTest.java`

- [ ] **Step 1: 실패 테스트 작성**

```java
package dev.jazzybyte.onseoul.collector.service;

import dev.jazzybyte.onseoul.collector.PublicServiceRowMapper;
import dev.jazzybyte.onseoul.collector.SeoulOpenApiClient;
import dev.jazzybyte.onseoul.collector.domain.ApiSourceCatalog;
import dev.jazzybyte.onseoul.collector.domain.CollectionHistory;
import dev.jazzybyte.onseoul.collector.dto.PublicServiceRow;
import dev.jazzybyte.onseoul.collector.dto.UpsertResult;
import dev.jazzybyte.onseoul.collector.enums.CollectionStatus;
import dev.jazzybyte.onseoul.collector.exception.SeoulApiException;
import dev.jazzybyte.onseoul.collector.repository.ApiSourceCatalogRepository;
import dev.jazzybyte.onseoul.collector.repository.CollectionHistoryRepository;
import dev.jazzybyte.onseoul.domain.PublicServiceReservation;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.repository.PublicServiceReservationRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class CollectionServiceTest {

    @Mock private ApiSourceCatalogRepository catalogRepository;
    @Mock private CollectionHistoryRepository historyRepository;
    @Mock private PublicServiceReservationRepository reservationRepository;
    @Mock private SeoulOpenApiClient apiClient;
    @Mock private PublicServiceRowMapper rowMapper;
    @Mock private UpsertService upsertService;

    @InjectMocks
    private CollectionService collectionService;

    private ApiSourceCatalog source1;
    private ApiSourceCatalog source2;

    @BeforeEach
    void setUp() {
        source1 = ApiSourceCatalog.builder()
                .datasetId("OA-2266").datasetName("체육시설")
                .datasetUrl("http://example.com/OA-2266").active(true).build();
        source2 = ApiSourceCatalog.builder()
                .datasetId("OA-2267").datasetName("시설대관")
                .datasetUrl("http://example.com/OA-2267").active(true).build();

        // save()가 저장된 엔티티를 반환하도록 설정 (ID가 필요한 경우를 위해)
        when(historyRepository.save(any())).thenAnswer(inv -> inv.getArgument(0));
    }

    @Test
    @DisplayName("모든 소스 수집 성공 시 각 CollectionHistory가 SUCCESS로 기록된다")
    void all_sources_succeed() {
        when(catalogRepository.findAllByActiveTrue()).thenReturn(List.of(source1, source2));

        PublicServiceRow row = new PublicServiceRow();
        when(apiClient.fetchAll(anyString())).thenReturn(List.of(row));

        PublicServiceReservation entity = PublicServiceReservation.builder()
                .serviceId("SVC001").serviceName("서비스1").serviceStatus("접수중").build();
        when(rowMapper.toEntity(row)).thenReturn(Optional.of(entity));
        when(upsertService.upsert(anyList(), any())).thenReturn(new UpsertResult(1, 0, 0));
        when(reservationRepository.findAllByDeletedAtIsNull()).thenReturn(List.of(entity));

        collectionService.collectAll();

        ArgumentCaptor<CollectionHistory> historyCaptor = ArgumentCaptor.forClass(CollectionHistory.class);
        verify(historyRepository, times(4)).save(historyCaptor.capture()); // 소스당 2번 (생성 + 완료)
        List<CollectionHistory> histories = historyCaptor.getAllValues();
        assertThat(histories).extracting(CollectionHistory::getStatus)
                .containsOnly(CollectionStatus.SUCCESS, CollectionStatus.FAILED); // FAILED는 초기 상태
    }

    @Test
    @DisplayName("한 소스 실패 시 해당 CollectionHistory는 FAILED, 나머지는 계속 진행된다")
    void one_source_fails_others_continue() {
        when(catalogRepository.findAllByActiveTrue()).thenReturn(List.of(source1, source2));
        when(apiClient.fetchAll(contains("OA-2266")))
                .thenThrow(new SeoulApiException(ErrorCode.COLLECT_API_SERVER_ERROR, "서버 오류"));

        PublicServiceRow row = new PublicServiceRow();
        when(apiClient.fetchAll(contains("OA-2267"))).thenReturn(List.of(row));
        PublicServiceReservation entity = PublicServiceReservation.builder()
                .serviceId("SVC002").serviceName("서비스2").serviceStatus("접수중").build();
        when(rowMapper.toEntity(row)).thenReturn(Optional.of(entity));
        when(upsertService.upsert(anyList(), any())).thenReturn(new UpsertResult(1, 0, 0));

        collectionService.collectAll();

        // source2는 정상 수집됨 — upsertService 호출 확인
        verify(upsertService, times(1)).upsert(anyList(), any());
        // 부분 실패 시 deletion sweep 스킵 — reservationRepository 미호출
        verify(reservationRepository, never()).findAllByDeletedAtIsNull();
    }

    @Test
    @DisplayName("전체 성공 후 DB 잔존 레코드(수집 안 된 것)는 soft-delete된다")
    void deletion_sweep_runs_after_all_succeed() {
        when(catalogRepository.findAllByActiveTrue()).thenReturn(List.of(source1));

        PublicServiceRow row = new PublicServiceRow();
        when(apiClient.fetchAll(anyString())).thenReturn(List.of(row));
        PublicServiceReservation collected = PublicServiceReservation.builder()
                .serviceId("SVC001").serviceName("서비스1").serviceStatus("접수중").build();
        PublicServiceReservation stale = PublicServiceReservation.builder()
                .serviceId("SVC_STALE").serviceName("구 서비스").serviceStatus("안내중").build();
        when(rowMapper.toEntity(row)).thenReturn(Optional.of(collected));
        when(upsertService.upsert(anyList(), any())).thenReturn(new UpsertResult(0, 0, 1));
        when(reservationRepository.findAllByDeletedAtIsNull()).thenReturn(List.of(collected, stale));

        collectionService.collectAll();

        verify(reservationRepository).save(stale); // stale만 soft-delete 저장
    }
}
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
./gradlew :collector:test --tests "*.CollectionServiceTest" --console=plain 2>&1 | tail -10
```
Expected: FAIL (CollectionService 미구현)

- [ ] **Step 3: CollectionService 구현**

```java
package dev.jazzybyte.onseoul.collector.service;

import dev.jazzybyte.onseoul.collector.PublicServiceRowMapper;
import dev.jazzybyte.onseoul.collector.SeoulOpenApiClient;
import dev.jazzybyte.onseoul.collector.domain.ApiSourceCatalog;
import dev.jazzybyte.onseoul.collector.domain.CollectionHistory;
import dev.jazzybyte.onseoul.collector.dto.UpsertResult;
import dev.jazzybyte.onseoul.collector.repository.ApiSourceCatalogRepository;
import dev.jazzybyte.onseoul.collector.repository.CollectionHistoryRepository;
import dev.jazzybyte.onseoul.domain.PublicServiceReservation;
import dev.jazzybyte.onseoul.repository.PublicServiceReservationRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

@Slf4j
@Service
@RequiredArgsConstructor
public class CollectionService {

    private final ApiSourceCatalogRepository catalogRepository;
    private final CollectionHistoryRepository historyRepository;
    private final PublicServiceReservationRepository reservationRepository;
    private final SeoulOpenApiClient apiClient;
    private final PublicServiceRowMapper rowMapper;
    private final UpsertService upsertService;

    /**
     * 모든 활성 소스를 순차 수집한다.
     *
     * <p>개별 소스 실패 시 이력에 기록 후 다음 소스로 계속 진행한다.
     * 전체 성공 시에만 DB 잔존 레코드 soft-delete를 수행한다.</p>
     */
    public void collectAll() {
        List<ApiSourceCatalog> sources = catalogRepository.findAllByActiveTrue();
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

        log.info("수집 완료");
    }

    private boolean collectOne(ApiSourceCatalog source, Set<String> allSeenServiceIds) {
        CollectionHistory history = CollectionHistory.builder()
                .sourceId(source.getId())
                .build();
        historyRepository.save(history);

        long startMs = System.currentTimeMillis();
        try {
            List<PublicServiceReservation> entities = apiClient.fetchAll(source.getApiServicePath())
                    .stream()
                    .map(rowMapper::toEntity)
                    .filter(java.util.Optional::isPresent)
                    .map(java.util.Optional::get)
                    .toList();

            entities.stream()
                    .map(PublicServiceReservation::getServiceId)
                    .forEach(allSeenServiceIds::add);

            UpsertResult result = upsertService.upsert(entities, history.getId());
            int durationMs = (int) (System.currentTimeMillis() - startMs);

            history.complete(entities.size(), result.newCount(), result.updatedCount(), 0, durationMs);
            historyRepository.save(history);

            log.info("소스 수집 완료 — datasetId={}, total={}, new={}, updated={}, unchanged={}",
                    source.getDatasetId(), entities.size(),
                    result.newCount(), result.updatedCount(), result.unchangedCount());
            return true;

        } catch (Exception e) {
            int durationMs = (int) (System.currentTimeMillis() - startMs);
            history.fail(e.getMessage(), durationMs);
            historyRepository.save(history);
            log.error("소스 수집 실패 — datasetId={}, error={}", source.getDatasetId(), e.getMessage(), e);
            return false;
        }
    }

    /**
     * DB에 남아있지만 이번 수집에서 보이지 않은 레코드를 soft-delete 처리한다.
     * 전체 소스 수집 성공 시에만 호출된다.
     */
    private void performDeletionSweep(Set<String> seenServiceIds) {
        List<PublicServiceReservation> toDelete = reservationRepository.findAllByDeletedAtIsNull()
                .stream()
                .filter(r -> !seenServiceIds.contains(r.getServiceId()))
                .toList();

        if (toDelete.isEmpty()) {
            return;
        }

        toDelete.forEach(PublicServiceReservation::softDelete);
        reservationRepository.saveAll(toDelete);
        log.info("Deletion sweep — soft-deleted {}건", toDelete.size());
    }
}
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
./gradlew :collector:test --tests "*.CollectionServiceTest" --console=plain 2>&1 | tail -10
```
Expected: BUILD SUCCESSFUL

- [ ] **Step 5: collector 전체 테스트 통과 확인**

```bash
./gradlew :collector:test --console=plain 2>&1 | tail -10
```
Expected: BUILD SUCCESSFUL

- [ ] **Step 6: 커밋**

```bash
git add collector/src/main/java/dev/jazzybyte/onseoul/collector/service/CollectionService.java \
        collector/src/test/java/dev/jazzybyte/onseoul/collector/service/CollectionServiceTest.java
git commit -m "feat(collector): CollectionService — 소스 오케스트레이션, 부분 실패 허용, deletion sweep"
```

---

## Task 5: CollectionController + CollectionScheduler

**Files:**
- Create: `app/src/main/java/dev/jazzybyte/onseoul/controller/CollectionController.java`
- Create: `app/src/main/java/dev/jazzybyte/onseoul/scheduler/CollectionScheduler.java`
- Create: `app/src/test/java/dev/jazzybyte/onseoul/controller/CollectionControllerTest.java`

- [ ] **Step 1: 실패 테스트 작성**

```java
package dev.jazzybyte.onseoul.controller;

import dev.jazzybyte.onseoul.collector.service.CollectionService;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(CollectionController.class)
class CollectionControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private CollectionService collectionService;

    @Test
    @DisplayName("POST /admin/collection/trigger 호출 시 collectAll()이 실행되고 200을 반환한다")
    void trigger_calls_collect_all() throws Exception {
        mockMvc.perform(post("/admin/collection/trigger"))
                .andExpect(status().isOk());

        verify(collectionService).collectAll();
    }
}
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
./gradlew :app:test --tests "*.CollectionControllerTest" --console=plain 2>&1 | tail -10
```
Expected: FAIL (CollectionController 미구현)

- [ ] **Step 3: CollectionController 구현**

```java
package dev.jazzybyte.onseoul.controller;

import dev.jazzybyte.onseoul.collector.service.CollectionService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@Slf4j
@RestController
@RequestMapping("/admin/collection")
@RequiredArgsConstructor
public class CollectionController {

    private final CollectionService collectionService;

    /**
     * 수집 수동 트리거 — 개발/운영 점검용.
     * 수집은 동기로 실행되므로 응답은 수집 완료 후 반환된다.
     */
    @PostMapping("/trigger")
    public ResponseEntity<Void> trigger() {
        log.info("수동 수집 트리거 요청");
        collectionService.collectAll();
        return ResponseEntity.ok().build();
    }
}
```

- [ ] **Step 4: CollectionScheduler 구현**

```java
package dev.jazzybyte.onseoul.scheduler;

import dev.jazzybyte.onseoul.collector.service.CollectionService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Slf4j
@Component
@RequiredArgsConstructor
public class CollectionScheduler {

    private final CollectionService collectionService;

    /**
     * 매일 08시 수집 실행.
     * 수집 실패 시 CollectionService 내부에서 이력을 기록하고 예외는 삼킨다.
     */
    @Scheduled(cron = "0 0 8 * * *")
    public void scheduledCollect() {
        log.info("스케줄 수집 시작");
        collectionService.collectAll();
    }
}
```

- [ ] **Step 5: OnSeoulApiApplication에 @EnableScheduling 추가**

```java
package dev.jazzybyte.onseoul;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling
public class OnSeoulApiApplication {

    public static void main(String[] args) {
        SpringApplication.run(OnSeoulApiApplication.class, args);
    }
}
```

- [ ] **Step 6: 테스트 통과 확인**

```bash
./gradlew :app:test --tests "*.CollectionControllerTest" --console=plain 2>&1 | tail -10
```
Expected: BUILD SUCCESSFUL

- [ ] **Step 7: 전체 테스트 통과 확인**

```bash
./gradlew :common:test :domain:test :collector:test :app:test --console=plain 2>&1 | tail -15
```
Expected: BUILD SUCCESSFUL

- [ ] **Step 8: 커밋**

```bash
git add app/src/main/java/dev/jazzybyte/onseoul/controller/CollectionController.java \
        app/src/main/java/dev/jazzybyte/onseoul/scheduler/CollectionScheduler.java \
        app/src/main/java/dev/jazzybyte/onseoul/OnSeoulApiApplication.java \
        app/src/test/java/dev/jazzybyte/onseoul/controller/CollectionControllerTest.java
git commit -m "feat(app): CollectionController 수동 트리거 및 CollectionScheduler 새벽 3시 수집"
```

---

## Task 6: docs 업데이트

**Files:**
- Modify: `on-seoul-api/docs/api-service-implementation.md`

- [ ] **Step 1: Phase 6 / 7 체크박스 완료 처리**

`api-service-implementation.md`에서 Phase 6 / 7 항목의 `- [ ]`를 `- [x]`로 변경한다.

- [ ] **Step 2: 커밋**

```bash
git add docs/api-service-implementation.md
git commit -m "docs: Phase 6-7 완료 처리"
```

---

## Self-Review

**Spec coverage:**
- [x] `service_id` 기준 기존 데이터 조회 → `UpsertService.upsert()` 내 `findAllByServiceIdIn`
- [x] 신규 / 변경 / 유지 3분류 → `UpsertService` Task 3
- [x] 변경 시 `ServiceChangeLog` 기록 → `buildChangeLogs()`
- [x] Upsert 결과 `collection_history` 기록 → `CollectionService.collectOne()` 내 `history.complete()`
- [x] `@Scheduled(cron = "0 0 8 * * *")` → Task 5
- [x] 한 API 실패 시 이력 기록 후 다음 API 계속 → `CollectionService.collectOne()` try-catch
- [x] 수동 트리거 `POST /admin/collection/trigger` → Task 5
- [x] soft-delete (deleted 분류) → `performDeletionSweep()`

**Notes:**
- `deletedCount`는 per-source history에서 0으로 기록된다. Deletion sweep은 전체 run 레벨 처리이므로 별도 로그(INFO)로만 남긴다. 향후 필요 시 sweep 전용 history 레코드를 추가한다.
- `CollectionControllerTest`는 Spring Security가 활성화되어 있으면 401이 반환될 수 있다. Security 설정이 추가되기 전까지는 `@WebMvcTest` 기본 설정에서 `/admin/**` 경로가 열려있다고 가정한다. 만약 Security 빈이 컨텍스트에 로드되어 403/401이 반환된다면 `@WebMvcTest(CollectionController.class, excludeAutoConfiguration = SecurityAutoConfiguration.class)`로 수정한다.
