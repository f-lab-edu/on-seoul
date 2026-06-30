package dev.jazzybyte.onseoul.util;

import org.springframework.web.util.HtmlUtils;

/**
 * 서울 Open API 원본 텍스트가 HTML-escape(&lt; &gt; &amp; &quot; &#39; &middot; 등 + numeric &#NN;)된 채
 * 저장되어 프론트(평문 렌더) 화면이 깨지는 문제를 해소하기 위한 디코딩 유틸.
 *
 * <p>{@link HtmlUtils#htmlUnescape(String)}는 named/numeric 엔티티를 모두 커버하며, 엔티티가 없으면
 * no-op이라 멱등하다. 디코딩은 1회만 수행한다(이중 디코딩 금지).
 */
public final class HtmlTextUtil {

    private HtmlTextUtil() {
    }

    /** HTML 엔티티를 디코딩한다. null은 null 반환, 엔티티 없으면 입력 그대로(멱등). */
    public static String unescape(String value) {
        if (value == null) {
            return null;
        }
        return HtmlUtils.htmlUnescape(value);
    }
}
