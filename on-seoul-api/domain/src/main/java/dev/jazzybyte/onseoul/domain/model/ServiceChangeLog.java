package dev.jazzybyte.onseoul.domain.model;

import lombok.Builder;
import lombok.Getter;

import java.time.LocalDateTime;

@Getter
public class ServiceChangeLog {

    private Long id;
    private String serviceId;
    private Long collectionId;
    private ChangeType changeType;
    private String fieldName;
    private String oldValue;
    private String newValue;
    private LocalDateTime changedAt;

    @Builder
    public ServiceChangeLog(Long id, String serviceId, Long collectionId, ChangeType changeType,
                            String fieldName, String oldValue, String newValue,
                            LocalDateTime changedAt) {
        this.id = id;
        this.serviceId = serviceId;
        this.collectionId = collectionId;
        this.changeType = changeType;
        this.fieldName = fieldName;
        this.oldValue = oldValue;
        this.newValue = newValue;
        this.changedAt = changedAt != null ? changedAt : LocalDateTime.now();
    }
}
