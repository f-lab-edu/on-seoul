package dev.jazzybyte.onseoul.domain.model;

import lombok.Builder;
import lombok.Getter;

import java.time.LocalDate;
import java.time.LocalDateTime;

@Getter
public class ApiSourceCatalog {

    private Long id;
    private String datasetId;
    private String datasetName;
    private String datasetUrl;
    private String apiServicePath;
    private boolean active;
    private String tags;
    private LocalDate metaUpdatedAt;
    private LocalDateTime dataUpdatedAt;
    private LocalDateTime createdAt;

    @Builder
    public ApiSourceCatalog(Long id, String datasetId, String datasetName, String datasetUrl,
                            String apiServicePath, boolean active, String tags,
                            LocalDate metaUpdatedAt, LocalDateTime dataUpdatedAt,
                            LocalDateTime createdAt) {
        this.id = id;
        this.datasetId = datasetId;
        this.datasetName = datasetName;
        this.datasetUrl = datasetUrl;
        this.apiServicePath = apiServicePath;
        this.active = active;
        this.tags = tags;
        this.metaUpdatedAt = metaUpdatedAt;
        this.dataUpdatedAt = dataUpdatedAt;
        this.createdAt = createdAt;
    }
}
