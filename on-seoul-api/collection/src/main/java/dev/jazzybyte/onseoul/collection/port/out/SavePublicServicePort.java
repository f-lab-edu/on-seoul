package dev.jazzybyte.onseoul.collection.port.out;

import dev.jazzybyte.onseoul.collection.domain.PublicServiceReservation;

import java.util.List;

public interface SavePublicServicePort {
    PublicServiceReservation save(PublicServiceReservation reservation);
    List<PublicServiceReservation> saveAll(List<PublicServiceReservation> reservations);
}
