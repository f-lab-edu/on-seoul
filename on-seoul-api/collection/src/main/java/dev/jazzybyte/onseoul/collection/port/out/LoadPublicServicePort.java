package dev.jazzybyte.onseoul.collection.port.out;

import dev.jazzybyte.onseoul.collection.domain.PublicServiceReservation;

import java.util.Collection;
import java.util.List;

public interface LoadPublicServicePort {
    List<PublicServiceReservation> findAllByDeletedAtIsNull();
    List<PublicServiceReservation> findAllByServiceIdIn(Collection<String> serviceIds);
    List<PublicServiceReservation> findAllByCoordXIsNullOrCoordYIsNull();
}
