package dev.jazzybyte.onseoul.adapter.out.persistence.reservation;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

interface PublicServiceReservationJpaRepository
        extends JpaRepository<PublicServiceReservationJpaEntity, Long> {

    Optional<PublicServiceReservationJpaEntity> findByServiceId(String serviceId);

    List<PublicServiceReservationJpaEntity> findAllByDeletedAtIsNull();

    List<PublicServiceReservationJpaEntity> findAllByServiceIdIn(Collection<String> serviceIds);

    List<PublicServiceReservationJpaEntity> findAllByCoordXIsNullOrCoordYIsNull();
}
