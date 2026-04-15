package dev.jazzybyte.onseoul.repository;

import dev.jazzybyte.onseoul.domain.PublicServiceReservation;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface PublicServiceReservationRepository extends JpaRepository<PublicServiceReservation, Long> {

    Optional<PublicServiceReservation> findByServiceId(String serviceId);

    List<PublicServiceReservation> findAllByDeletedAtIsNull();
}
