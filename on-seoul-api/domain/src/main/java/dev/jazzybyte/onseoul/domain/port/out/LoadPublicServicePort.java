package dev.jazzybyte.onseoul.domain.port.out;

import dev.jazzybyte.onseoul.domain.model.PublicServiceReservation;

import java.util.Collection;
import java.util.List;

public interface LoadPublicServicePort {
    List<PublicServiceReservation> findAllByDeletedAtIsNull();
    List<PublicServiceReservation> findAllByServiceIdIn(Collection<String> serviceIds);
    List<PublicServiceReservation> findAllByCoordXIsNullOrCoordYIsNull();
}
