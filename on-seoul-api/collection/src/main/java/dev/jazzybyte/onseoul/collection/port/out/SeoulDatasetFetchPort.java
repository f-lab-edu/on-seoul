package dev.jazzybyte.onseoul.collection.port.out;

import dev.jazzybyte.onseoul.collection.domain.PublicServiceReservation;

import java.util.List;

public interface SeoulDatasetFetchPort {
    List<PublicServiceReservation> fetchAll(String serviceName);
}
