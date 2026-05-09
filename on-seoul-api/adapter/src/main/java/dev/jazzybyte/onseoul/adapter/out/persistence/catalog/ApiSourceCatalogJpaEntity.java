package dev.jazzybyte.onseoul.adapter.out.persistence.catalog;

import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.CreationTimestamp;

import java.time.LocalDate;
import java.time.LocalDateTime;

@Entity
@Table(name = "api_source_catalog")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
class ApiSourceCatalogJpaEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "dataset_id", nullable = false, unique = true, length = 20)
    private String datasetId;

    @Column(name = "dataset_name", nullable = false, length = 100)
    private String datasetName;

    @Column(name = "dataset_url", nullable = false, columnDefinition = "TEXT")
    private String datasetUrl;

    @Column(name = "api_service_path", nullable = false, length = 100)
    private String apiServicePath;

    @Column(name = "is_active", nullable = false)
    private boolean active = true;

    @Column(name = "tags", length = 200)
    private String tags;

    @Column(name = "meta_updated_at")
    private LocalDate metaUpdatedAt;

    @Column(name = "data_updated_at")
    private LocalDateTime dataUpdatedAt;

    @CreationTimestamp
    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;
}
