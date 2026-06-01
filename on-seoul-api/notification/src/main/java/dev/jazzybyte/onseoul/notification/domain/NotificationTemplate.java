package dev.jazzybyte.onseoul.notification.domain;

import java.util.List;
import java.util.Map;

public class NotificationTemplate {

    private NotificationTemplate() {}

    /** N개 서비스 fallback body에서 본문에 나열할 서비스 최대 개수. 초과분은 "외 M건"으로 요약한다. */
    private static final int MAX_LISTED_SERVICES = 5;

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
     * Fallback: 정형 변수 치환. AI 호출 실패 시 사용한다.
     * 구독 1건에 매칭된 서비스 그룹 목록을 받아 요약 메시지를 만든다.
     * services가 비어 있으면(이 경로는 호출자가 사전 차단해야 함) 일반 안내문을 반환한다.
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
            return renderSingle(services.get(0));
        }
        return renderMultiple(services);
    }

    private static TemplateResult renderSingle(NotificationTemplateRequest.ServiceChangeGroup g) {
        String label = displayName(g);
        String title = "[서울공공서비스] " + label + " 변경 알림";

        StringBuilder body = new StringBuilder(changeSummary(g.changes()));
        if (g.serviceUrl() != null && !g.serviceUrl().isBlank()) {
            body.append(System.lineSeparator()).append("자세히 보기: ").append(g.serviceUrl());
        }
        return new TemplateResult(title, body.toString(), TemplateSource.FALLBACK);
    }

    private static TemplateResult renderMultiple(List<NotificationTemplateRequest.ServiceChangeGroup> services) {
        String title = "[서울공공서비스] 구독하신 " + services.size() + "개 서비스 변경 알림";

        StringBuilder body = new StringBuilder();
        int listed = Math.min(services.size(), MAX_LISTED_SERVICES);
        for (int i = 0; i < listed; i++) {
            NotificationTemplateRequest.ServiceChangeGroup g = services.get(i);
            if (i > 0) {
                body.append(System.lineSeparator());
            }
            body.append("- ").append(displayName(g)).append(": ").append(changeSummary(g.changes()));
            if (g.serviceUrl() != null && !g.serviceUrl().isBlank()) {
                body.append(" ").append(g.serviceUrl());
            }
        }
        int remaining = services.size() - listed;
        if (remaining > 0) {
            body.append(System.lineSeparator()).append("외 ").append(remaining).append("건");
        }
        return new TemplateResult(title, body.toString(), TemplateSource.FALLBACK);
    }

    /** serviceName이 있으면 우선, 없으면 serviceId로 표기. */
    private static String displayName(NotificationTemplateRequest.ServiceChangeGroup g) {
        return (g.serviceName() != null && !g.serviceName().isBlank()) ? g.serviceName() : g.serviceId();
    }

    /** 한 서비스의 변경 목록을 대표 1건 + (외 N건) 요약 문자열로 만든다. */
    private static String changeSummary(List<NotificationTemplateRequest.ChangeItem> changes) {
        if (changes.isEmpty()) {
            return "변경이 감지되었습니다.";
        }
        NotificationTemplateRequest.ChangeItem c = changes.get(0);
        String summary = summarizeChange(c);
        if (changes.size() > 1) {
            summary += " (외 " + (changes.size() - 1) + "건)";
        }
        return summary;
    }

    /** change_type별로 변경 1건을 사람이 읽을 수 있는 문장으로 변환한다. NEW/DELETED는 필드 값이 없을 수 있다. */
    private static String summarizeChange(NotificationTemplateRequest.ChangeItem c) {
        String type = c.changeType() == null ? "" : c.changeType().trim().toUpperCase();
        return switch (type) {
            case "NEW" -> "신규 등록되었습니다.";
            case "DELETED" -> "접수가 종료되었습니다.";
            default -> summarizeUpdate(c);
        };
    }

    /** UPDATED 요약. field/old/new 데이터가 없으면 'null' 노출 대신 일반 안내로 폴백한다. */
    private static String summarizeUpdate(NotificationTemplateRequest.ChangeItem c) {
        if (c.fieldName() == null && c.oldValue() == null && c.newValue() == null) {
            return "변경이 감지되었습니다.";
        }
        return fieldLabel(c.fieldName()) + " 이(가) "
                + c.oldValue() + " → " + c.newValue() + " 으로 변경되었습니다.";
    }

    /** field_name을 한글 라벨로 매핑한다. 매핑에 없거나 null이면 원본을 그대로 반환한다. */
    private static String fieldLabel(String fieldName) {
        if (fieldName == null) {
            return "";
        }
        return FIELD_LABELS.getOrDefault(fieldName, fieldName);
    }
}
