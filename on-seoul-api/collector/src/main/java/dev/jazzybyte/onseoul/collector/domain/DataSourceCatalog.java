package dev.jazzybyte.onseoul.collector.domain;

import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.CreationTimestamp;

import java.time.LocalDate;
import java.time.LocalDateTime;

@Entity
@Table(name = "data_source_catalog")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class DataSourceCatalog {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "dataset_id", nullable = false, unique = true, length = 20)
    private String datasetId;

    @Column(name = "api_name", nullable = false, length = 100)
    private String apiName;

    @Column(name = "api_url", nullable = false, columnDefinition = "TEXT")
    private String apiUrl;

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

    @Builder
    public DataSourceCatalog(String datasetId, String apiName, String apiUrl,
                             boolean active, String tags) {
        this.datasetId = datasetId;
        this.apiName = apiName;
        this.apiUrl = apiUrl;
        this.active = active;
        this.tags = tags;
    }
}
