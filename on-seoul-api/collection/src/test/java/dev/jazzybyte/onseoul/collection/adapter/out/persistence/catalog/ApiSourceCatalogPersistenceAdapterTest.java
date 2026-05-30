package dev.jazzybyte.onseoul.collection.adapter.out.persistence.catalog;

import dev.jazzybyte.onseoul.collection.domain.ApiSourceCatalog;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.jdbc.Sql;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DataJpaTest
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:testdb;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DEFAULT_NULL_ORDERING=HIGH",
        "spring.jpa.hibernate.ddl-auto=none",
        "spring.sql.init.mode=embedded"
})
@Import(ApiSourceCatalogPersistenceAdapter.class)
class ApiSourceCatalogPersistenceAdapterTest {

    @Autowired
    private ApiSourceCatalogPersistenceAdapter adapter;

    @Autowired
    private ApiSourceCatalogJpaRepository jpaRepository;

    @Test
    @DisplayName("findAllByActiveTrue — active=true 인 카탈로그만 반환한다")
    @Sql(statements = {
            "INSERT INTO api_source_catalog (dataset_id, dataset_name, dataset_url, api_service_path, is_active) VALUES ('OA-001', '문화행사', 'http://example.com/1', '/cultural', TRUE)",
            "INSERT INTO api_source_catalog (dataset_id, dataset_name, dataset_url, api_service_path, is_active) VALUES ('OA-002', '체육시설', 'http://example.com/2', '/sports', TRUE)",
            "INSERT INTO api_source_catalog (dataset_id, dataset_name, dataset_url, api_service_path, is_active) VALUES ('OA-003', '비활성화', 'http://example.com/3', '/inactive', FALSE)"
    })
    void findAllByActiveTrue_returnsOnlyActiveEntries() {
        List<ApiSourceCatalog> result = adapter.findAllByActiveTrue();

        assertThat(result).hasSize(2);
        assertThat(result).extracting(ApiSourceCatalog::getDatasetId)
                .containsExactlyInAnyOrder("OA-001", "OA-002");
        assertThat(result).noneMatch(c -> "OA-003".equals(c.getDatasetId()));
    }

    @Test
    @DisplayName("findAllByActiveTrue — active 카탈로그가 없으면 빈 리스트를 반환한다")
    @Sql(statements = {
            "INSERT INTO api_source_catalog (dataset_id, dataset_name, dataset_url, api_service_path, is_active) VALUES ('OA-099', '비활성화만', 'http://example.com/99', '/none', FALSE)"
    })
    void findAllByActiveTrue_noActiveEntries_returnsEmpty() {
        List<ApiSourceCatalog> result = adapter.findAllByActiveTrue();

        assertThat(result).isEmpty();
    }

    @Test
    @DisplayName("findAllByActiveTrue — 도메인 객체로 올바르게 매핑된다")
    @Sql(statements = {
            "INSERT INTO api_source_catalog (dataset_id, dataset_name, dataset_url, api_service_path, is_active, tags) VALUES ('OA-010', '교육프로그램', 'http://example.com/10', '/education', TRUE, '교육,문화')"
    })
    void findAllByActiveTrue_mapsFieldsCorrectly() {
        List<ApiSourceCatalog> result = adapter.findAllByActiveTrue();

        assertThat(result).hasSize(1);
        ApiSourceCatalog catalog = result.get(0);
        assertThat(catalog.getId()).isNotNull().isPositive();
        assertThat(catalog.getDatasetId()).isEqualTo("OA-010");
        assertThat(catalog.getDatasetName()).isEqualTo("교육프로그램");
        assertThat(catalog.getDatasetUrl()).isEqualTo("http://example.com/10");
        assertThat(catalog.getApiServicePath()).isEqualTo("/education");
        assertThat(catalog.isActive()).isTrue();
        assertThat(catalog.getTags()).isEqualTo("교육,문화");
        assertThat(catalog.getCreatedAt()).isNotNull();
    }

    @Test
    @DisplayName("findAllByActiveTrue — 전체 목록이 없으면 빈 리스트를 반환한다")
    void findAllByActiveTrue_emptyTable_returnsEmpty() {
        List<ApiSourceCatalog> result = adapter.findAllByActiveTrue();

        assertThat(result).isEmpty();
    }
}
