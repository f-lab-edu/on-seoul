package dev.jazzybyte.onseoul.adapter.out.persistence.collection;

import dev.jazzybyte.onseoul.domain.model.ChangeType;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.CreationTimestamp;

import java.time.LocalDateTime;

@Entity
@Table(name = "service_change_log")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
class ServiceChangeLogJpaEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "service_id", nullable = false, length = 30)
    private String serviceId;

    @Column(name = "collection_id", nullable = false)
    private Long collectionId;

    @Enumerated(EnumType.STRING)
    @Column(name = "change_type", nullable = false, length = 10)
    private ChangeType changeType;

    @Column(name = "field_name", length = 50)
    private String fieldName;

    @Column(name = "old_value", columnDefinition = "TEXT")
    private String oldValue;

    @Column(name = "new_value", columnDefinition = "TEXT")
    private String newValue;

    @CreationTimestamp
    @Column(name = "changed_at", nullable = false, updatable = false)
    private LocalDateTime changedAt;

    ServiceChangeLogJpaEntity(String serviceId, Long collectionId, ChangeType changeType,
                              String fieldName, String oldValue, String newValue) {
        this.serviceId = serviceId;
        this.collectionId = collectionId;
        this.changeType = changeType;
        this.fieldName = fieldName;
        this.oldValue = oldValue;
        this.newValue = newValue;
    }
}
