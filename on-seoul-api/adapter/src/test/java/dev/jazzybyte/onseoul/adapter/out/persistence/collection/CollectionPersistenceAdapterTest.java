package dev.jazzybyte.onseoul.adapter.out.persistence.collection;

import dev.jazzybyte.onseoul.domain.model.ChangeType;
import dev.jazzybyte.onseoul.domain.model.CollectionHistory;
import dev.jazzybyte.onseoul.domain.model.CollectionStatus;
import dev.jazzybyte.onseoul.domain.model.ServiceChangeLog;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;

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
        // 수집 이력 열기 (id=null → insert)
        CollectionHistory opened = adapter.save(CollectionHistory.create(1L));
        assertThat(opened.getId()).isNotNull().isPositive();

        // 수집 완료 후 결과 기록 (id 있음 → update)
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
        // 수집 이력 열기
        CollectionHistory opened = adapter.save(CollectionHistory.create(2L));

        // 실패 결과 기록 (id 있음 → update)
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

        // complete 후 동일 id로 업데이트
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
        // collection_history 먼저 저장 (FK 참조 가능성 고려)
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
}
