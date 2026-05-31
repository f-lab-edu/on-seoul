package dev.jazzybyte.onseoul.collection.adapter.out.persistence.collection;

import dev.jazzybyte.onseoul.collection.domain.ChangeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDateTime;
import java.util.List;

interface ServiceChangeLogJpaRepository extends JpaRepository<ServiceChangeLogJpaEntity, Long> {

    @Query("""
            SELECT DISTINCT l.serviceId FROM ServiceChangeLogJpaEntity l
            WHERE l.changedAt >= :since AND l.changeType IN :types
            """)
    List<String> findDistinctServiceIdsByChangedAtSinceAndTypeIn(
            @Param("since") LocalDateTime since,
            @Param("types") List<ChangeType> types);
}
