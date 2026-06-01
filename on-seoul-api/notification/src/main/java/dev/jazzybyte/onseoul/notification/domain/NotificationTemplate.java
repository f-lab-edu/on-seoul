package dev.jazzybyte.onseoul.notification.domain;

import java.util.List;
import java.util.Map;

/**
 * 알림 title + summary 생성용 도메인 헬퍼.
 *
 * <p>사실 정보(서비스명·상태·접수기간·링크·변경 표)는 Knock 이메일 템플릿이
 * 구조화 데이터({@link NotificationContent.ServiceCard})로 결정적 렌더링하므로,
 * 여기서 만드는 {@code summary}는 "N개 서비스 변경 안내" 수준의 짧은 요약이면 충분하다.
 *
 * <p>{@link #render(NotificationTemplateRequest)}는 AI 호출 실패 시 fallback summary를 만든다.
 */
public class NotificationTemplate {

    private NotificationTemplate() {}

    /**
     * change_log의 field_name → 한글 라벨 매핑.
     *
     * <p>수집 모듈(UpsertService)이 실제로 기록하는 키는 camelCase
     * ({@code serviceStatus}/{@code receiptStartDt}/{@code receiptEndDt})다.
     * 스키마/와이어가 snake_case로 전달될 수 있는 경로(AI 서비스 연동 등)를 함께 방어하기 위해
     * snake_case 별칭도 등록한다. 매핑에 없는 field_name은 원본을 그대로 사용한다.
     *
     * <p>service_status 값 자체는 별도 매핑하지 않는다. 저장 값은 서울 열린데이터 광장의
     * {@code SVCSTATNM}(예: "접수중", "예약마감")으로 이미 한글 표시명이므로 추가 변환이 불필요하다.
     */
    private static final Map<String, String> FIELD_LABELS = Map.ofEntries(
            Map.entry("serviceStatus", "모집상태"),
            Map.entry("service_status", "모집상태"),
            Map.entry("receiptStartDt", "접수 시작일"),
            Map.entry("receipt_start_dt", "접수 시작일"),
            Map.entry("receiptEndDt", "접수 마감일"),
            Map.entry("receipt_end_dt", "접수 마감일"),
            Map.entry("serviceOpenStartDt", "서비스 시작일"),
            Map.entry("service_open_start_dt", "서비스 시작일"),
            Map.entry("serviceOpenEndDt", "서비스 종료일"),
            Map.entry("service_open_end_dt", "서비스 종료일"),
            Map.entry("paymentType", "결제유형"),
            Map.entry("payment_type", "결제유형"),
            Map.entry("placeName", "장소"),
            Map.entry("place_name", "장소"),
            Map.entry("areaName", "지역"),
            Map.entry("area_name", "지역"));

    /**
     * field_name을 한글 라벨로 매핑한다. 매핑에 없거나 null이면 원본을 그대로 반환한다.
     *
     * <p>Knock data 페이로드의 {@code changes[].label} 생성 시 재사용한다 —
     * camelCase field_name을 그대로 노출하지 않기 위함이다.
     */
    public static String fieldLabel(String fieldName) {
        if (fieldName == null) {
            return "";
        }
        return FIELD_LABELS.getOrDefault(fieldName, fieldName);
    }

    /**
     * Fallback: AI 호출 실패 시 결정적 title + summary를 만든다.
     * 구독 1건에 매칭된 서비스 그룹 목록을 받는다.
     * 사실 표/카드는 Knock이 그리므로 summary는 변경 서비스 개수 안내 수준이면 충분하다.
     */
    public static TemplateResult render(NotificationTemplateRequest req) {
        List<NotificationTemplateRequest.ServiceChangeGroup> services = req.services();

        if (services.isEmpty()) {
            return new TemplateResult(
                    "[서울공공서비스] 변경 알림",
                    "구독하신 서비스에 변경이 감지되었습니다.",
                    TemplateSource.FALLBACK);
        }

        if (services.size() == 1) {
            String label = displayName(services.get(0));
            return new TemplateResult(
                    "[서울공공서비스] " + label + " 변경 알림",
                    label + " 서비스에 변경이 감지되었습니다.",
                    TemplateSource.FALLBACK);
        }

        return new TemplateResult(
                "[서울공공서비스] 구독하신 " + services.size() + "개 서비스 변경 알림",
                "구독하신 " + services.size() + "개 서비스에 변경이 감지되었습니다.",
                TemplateSource.FALLBACK);
    }

    /** serviceName이 있으면 우선, 없으면 serviceId로 표기. */
    private static String displayName(NotificationTemplateRequest.ServiceChangeGroup g) {
        return (g.serviceName() != null && !g.serviceName().isBlank()) ? g.serviceName() : g.serviceId();
    }
}
