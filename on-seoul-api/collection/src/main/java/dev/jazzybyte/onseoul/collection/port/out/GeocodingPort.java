package dev.jazzybyte.onseoul.collection.port.out;

import java.math.BigDecimal;
import java.util.Optional;

public interface GeocodingPort {
    /** Returns [x(longitude), y(latitude)] or empty if geocoding fails. */
    Optional<BigDecimal[]> geocode(String placeName);
}
