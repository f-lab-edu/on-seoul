package dev.jazzybyte.onseoul.collection.adapter.out.persistence.reservation;

import dev.jazzybyte.onseoul.collection.domain.PublicServiceReservation;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;

@DataJpaTest
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:testdb;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DEFAULT_NULL_ORDERING=HIGH",
        "spring.jpa.hibernate.ddl-auto=none",
        "spring.sql.init.mode=embedded"
})
@Import({PublicServiceTextBackfill.class,
        PublicServiceReservationPersistenceAdapter.class,
        PublicServiceReservationPersistenceMapper.class})
class PublicServiceTextBackfillTest {

    @Autowired
    private PublicServiceTextBackfill backfill;

    @Autowired
    private PublicServiceReservationPersistenceAdapter adapter;

    private void saveEscaped(String serviceId) {
        adapter.save(PublicServiceReservation.builder()
                .serviceId(serviceId)
                .serviceGubun("A &amp; B")
                .serviceName("&lt;(아동)&gt; 프로그램")
                .serviceStatus("접수중")
                .paymentType("무료")
                .detailContent("It&#39;s &quot;상세&quot;")
                .placeName("서울&middot;중구")
                .serviceUrl("https://x?a=1&amp;b=2")
                .lastSyncedAt(LocalDateTime.now())
                .build());
    }

    @Test
    @DisplayName("run() — 기존 행의 표시 텍스트(+URL)를 디코딩하고 변경 건수를 반환한다")
    void run_decodesTextColumns() {
        saveEscaped("SVC-A");
        saveEscaped("SVC-B");

        PublicServiceTextBackfill.BackfillResult result = backfill.run();

        assertThat(result.processed()).isEqualTo(2);
        assertThat(result.changed()).isEqualTo(2);

        PublicServiceReservation row = adapter.findAllByServiceIdIn(java.util.List.of("SVC-A")).get(0);
        assertThat(row.getServiceGubun()).isEqualTo("A & B");
        assertThat(row.getServiceName()).isEqualTo("<(아동)> 프로그램");
        assertThat(row.getDetailContent()).isEqualTo("It's \"상세\"");
        assertThat(row.getPlaceName()).isEqualTo("서울·중구");
        assertThat(row.getServiceUrl()).isEqualTo("https://x?a=1&b=2");
    }

    @Test
    @DisplayName("run() — 이미 디코딩된 행은 변경 0건(멱등 재실행 안전)")
    void run_isIdempotent() {
        saveEscaped("SVC-C");
        backfill.run();

        PublicServiceTextBackfill.BackfillResult second = backfill.run();

        assertThat(second.processed()).isEqualTo(1);
        assertThat(second.changed()).isZero();
    }
}
