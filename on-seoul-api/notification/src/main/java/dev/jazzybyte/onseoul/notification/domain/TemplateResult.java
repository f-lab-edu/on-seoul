package dev.jazzybyte.onseoul.notification.domain;

/**
 * 알림 메시지 생성 결과 (title + summary).
 *
 * <p>사실 정보(서비스 표/카드)는 Knock 템플릿이 결정적으로 렌더링하므로
 * {@code summary}는 짧은 요약/하이라이트만 담는다.
 */
public record TemplateResult(String title, String summary, TemplateSource source) {

    public boolean isValid() {
        return title != null && !title.isBlank() && summary != null && !summary.isBlank();
    }
}
