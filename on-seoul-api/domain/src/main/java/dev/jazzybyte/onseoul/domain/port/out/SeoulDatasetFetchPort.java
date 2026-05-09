package dev.jazzybyte.onseoul.domain.port.out;

import dev.jazzybyte.onseoul.domain.model.PublicServiceReservation;

import java.util.List;

public interface SeoulDatasetFetchPort {
    List<PublicServiceReservation> fetchAll(String serviceName);
}
