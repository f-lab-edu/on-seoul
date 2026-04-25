package dev.jazzybyte.onseoul.adapter.out.seoulapi;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.domain.model.PublicServiceReservation;
import dev.jazzybyte.onseoul.domain.port.out.SeoulDatasetFetchPort;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpStatusCode;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
import reactor.util.retry.Retry;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;

@Slf4j
@Component
class SeoulOpenApiAdapter implements SeoulDatasetFetchPort {

    private final WebClient webClient;
    private final SeoulApiProperties properties;
    private final ObjectMapper objectMapper;
    private final PublicServiceRowMapper rowMapper;
    private final Retry retrySpec;

    SeoulOpenApiAdapter(WebClient seoulWebClient,
                        SeoulApiProperties properties,
                        ObjectMapper objectMapper,
                        PublicServiceRowMapper rowMapper) {
        this.webClient = seoulWebClient;
        this.properties = properties;
        this.objectMapper = objectMapper;
        this.rowMapper = rowMapper;
        this.retrySpec = Retry.backoff(properties.getMaxRetries(), Duration.ofSeconds(1))
                .maxBackoff(Duration.ofSeconds(properties.getMaxBackoffSeconds()))
                .filter(ex -> ex instanceof SeoulApiServerException);
    }

    // Test constructor with custom retrySpec
    SeoulOpenApiAdapter(WebClient seoulWebClient, SeoulApiProperties properties,
                        ObjectMapper objectMapper, PublicServiceRowMapper rowMapper,
                        Retry retrySpec) {
        this.webClient = seoulWebClient;
        this.properties = properties;
        this.objectMapper = objectMapper;
        this.rowMapper = rowMapper;
        this.retrySpec = retrySpec;
    }

    @Override
    public List<PublicServiceReservation> fetchAll(String serviceName) {
        int pageSize = properties.getPageSize();
        SeoulApiResponse firstPage = fetchPage(serviceName, 1, pageSize);
        int totalCount = firstPage.getListTotalCount();

        if (totalCount == 0 || firstPage.getRows().isEmpty()) {
            log.info("서울시 Open API 수집 결과 없으므로 수집 생략. serviceName={}", serviceName);
            return List.of();
        }

        log.info("서울시 Open API 수집 시작: serviceName={}, 전체={}건", serviceName, totalCount);
        List<PublicServiceReservation> result = new ArrayList<>(totalCount);

        firstPage.getRows().stream()
                .map(rowMapper::toEntity)
                .filter(java.util.Optional::isPresent)
                .map(java.util.Optional::get)
                .forEach(result::add);

        for (int start = pageSize + 1; start <= totalCount; start += pageSize) {
            SeoulApiResponse page = fetchPage(serviceName, start, Math.min(start + pageSize - 1, totalCount));
            page.getRows().stream()
                    .map(rowMapper::toEntity)
                    .filter(java.util.Optional::isPresent)
                    .map(java.util.Optional::get)
                    .forEach(result::add);
            log.debug("페이지 수집 완료: {}-{} / 전체 {}", start, start + page.getRows().size() - 1, totalCount);
        }

        return result;
    }

    SeoulApiResponse fetchPage(String serviceName, int startIndex, int endIndex) {
        return webClient.get()
                .uri("/{key}/json/{serviceName}/{start}/{end}/",
                        properties.getKey(), serviceName, startIndex, endIndex)
                .retrieve()
                .onStatus(HttpStatusCode::is5xxServerError,
                        resp -> Mono.error(
                                new SeoulApiServerException("서울 API 서버 오류: " + resp.statusCode())))
                .onStatus(HttpStatusCode::is4xxClientError,
                        resp -> Mono.error(
                                new OnSeoulApiException(ErrorCode.COLLECT_API_CLIENT_ERROR,
                                        "서울 API 클라이언트 오류: " + resp.statusCode())))
                .bodyToMono(String.class)
                .switchIfEmpty(Mono.error(new OnSeoulApiException(ErrorCode.COLLECT_API_PARSE_ERROR,
                        "빈 응답: serviceName=" + serviceName)))
                .onErrorMap(java.util.concurrent.TimeoutException.class,
                        e -> new OnSeoulApiException(ErrorCode.COLLECT_API_TIMEOUT,
                                "응답 시간 초과: serviceName=" + serviceName))
                .map(body -> parseResponse(body, serviceName))
                .retryWhen(retrySpec)
                .block(properties.getBlockTimeout());
    }

    private SeoulApiResponse parseResponse(String body, String serviceName) {
        try {
            JsonNode root = objectMapper.readTree(body);
            JsonNode inner = root.get(serviceName);
            if (inner == null || inner.isNull()) {
                throw new OnSeoulApiException(ErrorCode.COLLECT_API_PARSE_ERROR,
                        "API 응답에서 서비스 키를 찾을 수 없음: " + serviceName);
            }
            SeoulApiResponse response = objectMapper.treeToValue(inner, SeoulApiResponse.class);
            if (response.isNoData()) {
                log.info("수집 데이터 없음 — serviceName={}", serviceName);
                return response;
            }
            if (!response.isSuccess()) {
                throw new OnSeoulApiException(ErrorCode.COLLECT_API_CLIENT_ERROR,
                        "API 오류 코드: " + response.getResult().getCode()
                                + " / " + response.getResult().getMessage());
            }
            return response;
        } catch (OnSeoulApiException e) {
            throw e;
        } catch (Exception e) {
            throw new OnSeoulApiException(ErrorCode.COLLECT_API_PARSE_ERROR,
                    "API 응답 파싱 실패: " + e.getMessage(), e);
        }
    }
}
