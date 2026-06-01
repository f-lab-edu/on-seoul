package dev.jazzybyte.onseoul.notification.domain;

import java.util.List;

/**
 * 알림 발송 콘텐츠 값 객체.
 *
 * <p>구조화 데이터(서비스 표/카드) + AI 요약(summary)을 함께 담는다.
 * 사실 정보(서비스명·상태·접수기간·링크)는 {@link ServiceCard}로 결정적으로 표현하고,
 * AI는 짧은 요약/하이라이트({@code summary})만 생성한다.
 *
 * <p>도메인은 순수 Java만 안다 — JSON/Knock 직렬화는 어댑터/매퍼 계층 책임이다.
 */
public record NotificationContent(
        String title,
        String summary,
        List<ServiceCard> services
) {
    public NotificationContent {
        services = services == null ? List.of() : List.copyOf(services);
    }

    /** 한 서비스의 결정적 사실 카드. 변경 라인은 한글 라벨로 표기된 {@link ChangeLine} 목록이다. */
    public record ServiceCard(
            String name,
            String status,
            String area,
            String place,
            String target,
            String receiptStart,
            String receiptEnd,
            String url,
            String imageUrl,
            List<ChangeLine> changes
    ) {
        public ServiceCard {
            changes = changes == null ? List.of() : List.copyOf(changes);
        }
    }

    /** 한 건의 변경 라인. {@code label}은 한글 라벨(camelCase field_name 노출 금지). */
    public record ChangeLine(
            String label,
            String oldValue,
            String newValue
    ) {}
}
