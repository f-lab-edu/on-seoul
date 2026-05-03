package dev.jazzybyte.onseoul.domain.port.out;

import dev.jazzybyte.onseoul.domain.model.PublicServiceReservation;

import java.util.List;

public interface SavePublicServicePort {
    PublicServiceReservation save(PublicServiceReservation reservation);
    List<PublicServiceReservation> saveAll(List<PublicServiceReservation> reservations);
}
