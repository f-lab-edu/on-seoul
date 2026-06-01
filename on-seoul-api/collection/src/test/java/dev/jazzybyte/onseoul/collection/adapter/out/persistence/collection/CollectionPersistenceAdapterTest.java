package dev.jazzybyte.onseoul.collection.adapter.out.persistence.collection;

import dev.jazzybyte.onseoul.collection.domain.ChangeType;
import dev.jazzybyte.onseoul.collection.domain.CollectionHistory;
import dev.jazzybyte.onseoul.collection.domain.CollectionStatus;
import dev.jazzybyte.onseoul.collection.domain.ServiceChangeLog;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;
import dev.jazzybyte.onseoul.collection.port.out.LoadChangedServiceIdsPort.ChangedServiceIds;

import java.time.Instant;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DataJpaTest
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:testdb;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DEFAULT_NULL_ORDERING=HIGH",
        "spring.jpa.hibernate.ddl-auto=none",
        "spring.sql.init.mode=embedded"
})
@Import(CollectionPersistenceAdapter.class)
class CollectionPersistenceAdapterTest {

    @Autowired
    private CollectionPersistenceAdapter adapter;

    @Autowired
    private CollectionHistoryJpaRepository historyRepository;

    @Autowired
    private ServiceChangeLogJpaRepository changeLogRepository;

    // ── save(CollectionHistory) ───────────────────────────────────

    @Test
    @DisplayName("save(CollectionHistory) — SUCCESS 상태 저장")
    void save_successStatus_persistsCorrectly() {
        CollectionHistory opened = adapter.save(CollectionHistory.create(1L));
        assertThat(opened.getId()).isNotNull().isPositive();

        opened.complete(150, 20, 10, 3, 5000);
        CollectionHistory saved = adapter.save(opened);

        assertThat(saved.getId()).isEqualTo(opened.getId());
        assertThat(saved.getSourceId()).isEqualTo(1L);
        assertThat(saved.getStatus()).isEqualTo(CollectionStatus.SUCCESS);
        assertThat(saved.getTotalFetched()).isEqualTo(150);
        assertThat(saved.getNewCount()).isEqualTo(20);
        assertThat(saved.getUpdatedCount()).isEqualTo(10);
        assertThat(saved.getDeletedCount()).isEqualTo(3);
        assertThat(saved.getDurationMs()).isEqualTo(5000);
        assertThat(saved.getErrorMessage()).isNull();
        assertThat(saved.getCollectedAt()).isNotNull();
    }

    @Test
    @DisplayName("save(CollectionHistory) — FAILED 상태 + 에러 메시지 저장")
    void save_failedStatus_persistsErrorMessage() {
        CollectionHistory opened = adapter.save(CollectionHistory.create(2L));

        opened.fail("서울 Open API 응답 오류: 503 Service Unavailable", 1200);
        CollectionHistory saved = adapter.save(opened);

        assertThat(saved.getId()).isEqualTo(opened.getId());
        assertThat(saved.getSourceId()).isEqualTo(2L);
        assertThat(saved.getStatus()).isEqualTo(CollectionStatus.FAILED);
        assertThat(saved.getErrorMessage()).isEqualTo("서울 Open API 응답 오류: 503 Service Unavailable");
        assertThat(saved.getDurationMs()).isEqualTo(1200);
        assertThat(saved.getTotalFetched()).isZero();
    }

    @Test
    @DisplayName("save(CollectionHistory) — 초기 상태로 저장 후 완료 업데이트 시 동일 id로 갱신된다")
    void save_updateExisting_retainsSameId() {
        CollectionHistory initial = CollectionHistory.create(3L);
        CollectionHistory saved = adapter.save(initial);
        Long savedId = saved.getId();

        saved.complete(100, 15, 5, 2, 3000);
        CollectionHistory updated = adapter.save(saved);

        assertThat(updated.getId()).isEqualTo(savedId);
        assertThat(updated.getStatus()).isEqualTo(CollectionStatus.SUCCESS);
        assertThat(historyRepository.count()).isEqualTo(1);
    }

    // ── saveAll(List<ServiceChangeLog>) ──────────────────────────

    @Test
    @DisplayName("saveAll(List<ServiceChangeLog>) — 복수 로그 저장")
    void saveAll_multipleLogs_allPersisted() {
        CollectionHistory history = CollectionHistory.create(1L);
        CollectionHistory savedHistory = adapter.save(history);
        Long collectionId = savedHistory.getId();

        List<ServiceChangeLog> logs = List.of(
                ServiceChangeLog.builder()
                        .serviceId("SVC-001")
                        .collectionId(collectionId)
                        .changeType(ChangeType.NEW)
                        .build(),
                ServiceChangeLog.builder()
                        .serviceId("SVC-002")
                        .collectionId(collectionId)
                        .changeType(ChangeType.UPDATED)
                        .fieldName("serviceStatus")
                        .oldValue("접수중")
                        .newValue("마감")
                        .build(),
                ServiceChangeLog.builder()
                        .serviceId("SVC-003")
                        .collectionId(collectionId)
                        .changeType(ChangeType.DELETED)
                        .build()
        );

        adapter.saveAll(logs);

        assertThat(changeLogRepository.count()).isEqualTo(3);
        List<ServiceChangeLogJpaEntity> persisted = changeLogRepository.findAll();
        assertThat(persisted).extracting(ServiceChangeLogJpaEntity::getServiceId)
                .containsExactlyInAnyOrder("SVC-001", "SVC-002", "SVC-003");
        assertThat(persisted).extracting(ServiceChangeLogJpaEntity::getChangeType)
                .containsExactlyInAnyOrder(ChangeType.NEW, ChangeType.UPDATED, ChangeType.DELETED);

        ServiceChangeLogJpaEntity updatedLog = persisted.stream()
                .filter(l -> "SVC-002".equals(l.getServiceId()))
                .findFirst().orElseThrow();
        assertThat(updatedLog.getFieldName()).isEqualTo("serviceStatus");
        assertThat(updatedLog.getOldValue()).isEqualTo("접수중");
        assertThat(updatedLog.getNewValue()).isEqualTo("마감");
    }

    // ── loadSince(Instant) — Phase 7 임베딩 동기화 변경 service_id 조회 ──────

    /**
     * since 이전에 저장된 NEW/UPDATED/DELETED 행을 조회 → upsert=(NEW∪UPDATED) distinct,
     * delete=DELETED distinct 로 분류된다. @CreationTimestamp 와 Instant→LocalDateTime
     * 변환을 거치는 실제 JPQL round-trip을 검증한다(mock 단위 테스트가 못 잡는 영역).
     */
    @Test
    @DisplayName("loadSince — NEW/UPDATED는 upsert, DELETED는 delete로 분류하고 service_id를 distinct로 반환한다")
    void loadSince_classifiesAndDeduplicates() {
        Long collectionId = adapter.save(CollectionHistory.create(1L)).getId();
        Instant before = Instant.now().minusSeconds(5);

        adapter.saveAll(List.of(
                log(collectionId, "SVC-NEW", ChangeType.NEW),
                // 같은 service_id에 대해 UPDATED 행이 여러 개(필드별) 있어도 distinct 처리되어야 한다
                logField(collectionId, "SVC-UPD", "serviceStatus", "접수중", "마감"),
                logField(collectionId, "SVC-UPD", "receiptEndDt", "2026-01-01", "2026-02-01"),
                log(collectionId, "SVC-DEL", ChangeType.DELETED),
                // DELETED 도 중복될 수 있다
                log(collectionId, "SVC-DEL", ChangeType.DELETED)
        ));

        ChangedServiceIds changed = adapter.loadSince(before);

        assertThat(changed.upsert()).containsExactlyInAnyOrder("SVC-NEW", "SVC-UPD");
        assertThat(changed.delete()).containsExactly("SVC-DEL");
        assertThat(changed.isEmpty()).isFalse();
    }

    @Test
    @DisplayName("loadSince — since 이후 행만 대상 (since가 미래면 빈 결과)")
    void loadSince_excludesRowsBeforeSince() {
        Long collectionId = adapter.save(CollectionHistory.create(1L)).getId();
        adapter.saveAll(List.of(
                log(collectionId, "SVC-OLD-NEW", ChangeType.NEW),
                log(collectionId, "SVC-OLD-DEL", ChangeType.DELETED)
        ));

        // since를 미래로 잡으면 changed_at >= since 를 만족하는 행이 없다
        Instant future = Instant.now().plusSeconds(60);
        ChangedServiceIds changed = adapter.loadSince(future);

        assertThat(changed.upsert()).isEmpty();
        assertThat(changed.delete()).isEmpty();
        assertThat(changed.isEmpty()).isTrue();
    }

    @Test
    @DisplayName("loadSince — 변경 이력이 전혀 없으면 빈 결과를 반환한다")
    void loadSince_noLogs_returnsEmpty() {
        ChangedServiceIds changed = adapter.loadSince(Instant.now().minusSeconds(5));

        assertThat(changed.upsert()).isEmpty();
        assertThat(changed.delete()).isEmpty();
        assertThat(changed.isEmpty()).isTrue();
    }

    private static ServiceChangeLog log(Long collectionId, String serviceId, ChangeType type) {
        return ServiceChangeLog.builder()
                .serviceId(serviceId)
                .collectionId(collectionId)
                .changeType(type)
                .build();
    }

    private static ServiceChangeLog logField(Long collectionId, String serviceId,
                                             String field, String oldValue, String newValue) {
        return ServiceChangeLog.builder()
                .serviceId(serviceId)
                .collectionId(collectionId)
                .changeType(ChangeType.UPDATED)
                .fieldName(field)
                .oldValue(oldValue)
                .newValue(newValue)
                .build();
    }
}
