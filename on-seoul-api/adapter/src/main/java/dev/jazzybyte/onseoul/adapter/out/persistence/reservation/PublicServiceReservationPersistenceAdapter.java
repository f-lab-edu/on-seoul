package dev.jazzybyte.onseoul.adapter.out.persistence.reservation;

import dev.jazzybyte.onseoul.domain.model.PublicServiceReservation;
import dev.jazzybyte.onseoul.domain.port.out.LoadPublicServicePort;
import dev.jazzybyte.onseoul.domain.port.out.SavePublicServicePort;
import org.springframework.stereotype.Component;

import java.util.Collection;
import java.util.List;

@Component
class PublicServiceReservationPersistenceAdapter
        implements LoadPublicServicePort, SavePublicServicePort {

    private final PublicServiceReservationJpaRepository jpaRepository;
    private final PublicServiceReservationPersistenceMapper mapper;

    PublicServiceReservationPersistenceAdapter(
            final PublicServiceReservationJpaRepository jpaRepository,
            final PublicServiceReservationPersistenceMapper mapper) {
        this.jpaRepository = jpaRepository;
        this.mapper = mapper;
    }

    @Override
    public List<PublicServiceReservation> findAllByDeletedAtIsNull() {
        return jpaRepository.findAllByDeletedAtIsNull().stream()
                .map(mapper::toDomain)
                .toList();
    }

    @Override
    public List<PublicServiceReservation> findAllByServiceIdIn(Collection<String> serviceIds) {
        return jpaRepository.findAllByServiceIdIn(serviceIds).stream()
                .map(mapper::toDomain)
                .toList();
    }

    @Override
    public List<PublicServiceReservation> findAllByCoordXIsNullOrCoordYIsNull() {
        return jpaRepository.findAllByCoordXIsNullOrCoordYIsNull().stream()
                .map(mapper::toDomain)
                .toList();
    }

    @Override
    public PublicServiceReservation save(PublicServiceReservation reservation) {
        PublicServiceReservationJpaEntity entity;
        if (reservation.getId() != null) {
            entity = jpaRepository.findById(reservation.getId())
                    .map(e -> mapper.updateEntity(e, reservation))
                    .orElseGet(() -> mapper.toEntity(reservation));
        } else {
            // Check by serviceId for upsert logic
            entity = jpaRepository.findByServiceId(reservation.getServiceId())
                    .map(e -> mapper.updateEntity(e, reservation))
                    .orElseGet(() -> mapper.toEntity(reservation));
        }
        return mapper.toDomain(jpaRepository.save(entity));
    }

    @Override
    public List<PublicServiceReservation> saveAll(List<PublicServiceReservation> reservations) {
        List<PublicServiceReservationJpaEntity> entities = reservations.stream()
                .map(r -> {
                    if (r.getId() != null) {
                        return jpaRepository.findById(r.getId())
                                .map(e -> mapper.updateEntity(e, r))
                                .orElseGet(() -> mapper.toEntity(r));
                    }
                    return mapper.toEntity(r);
                })
                .toList();
        return jpaRepository.saveAll(entities).stream()
                .map(mapper::toDomain)
                .toList();
    }
}
