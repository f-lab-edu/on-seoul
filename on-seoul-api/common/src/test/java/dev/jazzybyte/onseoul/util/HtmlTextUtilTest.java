package dev.jazzybyte.onseoul.util;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class HtmlTextUtilTest {

    @Test
    @DisplayName("named 엔티티(<, >, &)를 디코딩한다")
    void unescape_namedEntities() {
        assertThat(HtmlTextUtil.unescape("&lt;(아동)&gt;")).isEqualTo("<(아동)>");
        assertThat(HtmlTextUtil.unescape("A &amp; B")).isEqualTo("A & B");
        assertThat(HtmlTextUtil.unescape("&quot;인용&quot;")).isEqualTo("\"인용\"");
    }

    @Test
    @DisplayName("numeric 참조(&#39; &#183;)를 디코딩한다")
    void unescape_numericEntities() {
        assertThat(HtmlTextUtil.unescape("It&#39;s")).isEqualTo("It's");
        // &middot; == &#183;
        assertThat(HtmlTextUtil.unescape("A&middot;B")).isEqualTo("A·B");
        assertThat(HtmlTextUtil.unescape("A&#183;B")).isEqualTo("A·B");
    }

    @Test
    @DisplayName("엔티티가 없으면 입력을 그대로 반환한다(no-op)")
    void unescape_noEntities_isNoOp() {
        assertThat(HtmlTextUtil.unescape("일반 텍스트 123")).isEqualTo("일반 텍스트 123");
    }

    @Test
    @DisplayName("이미 디코딩된 값을 다시 디코딩해도 변하지 않는다(멱등)")
    void unescape_idempotent() {
        String once = HtmlTextUtil.unescape("&lt;(아동)&gt;");
        String twice = HtmlTextUtil.unescape(once);
        assertThat(twice).isEqualTo(once).isEqualTo("<(아동)>");
    }

    @Test
    @DisplayName("null 입력은 null을 반환한다")
    void unescape_null_returnsNull() {
        assertThat(HtmlTextUtil.unescape(null)).isNull();
    }

    @Test
    @DisplayName("빈 문자열은 그대로 반환한다")
    void unescape_emptyString_returnsEmpty() {
        assertThat(HtmlTextUtil.unescape("")).isEqualTo("");
        assertThat(HtmlTextUtil.unescape("   ")).isEqualTo("   ");
    }

    @Test
    @DisplayName("hex numeric 참조(&#x27; &#xB7;)를 디코딩한다")
    void unescape_hexNumericEntities() {
        // &#x27; == &#39; == '
        assertThat(HtmlTextUtil.unescape("It&#x27;s")).isEqualTo("It's");
        // &#xB7; == &#183; == &middot; == ·
        assertThat(HtmlTextUtil.unescape("A&#xB7;B")).isEqualTo("A·B");
    }

    @Test
    @DisplayName("이중 인코딩 값은 1회만 디코딩한다(&amp;lt; → &lt;, < 아님)")
    void unescape_doubleEncoded_decodesOnce() {
        // 핵심 불변식: 한 번에 한 단계만 풀린다. &amp;lt; 의 &amp; 만 & 로 풀려 &lt; 가 남는다.
        assertThat(HtmlTextUtil.unescape("&amp;lt;")).isEqualTo("&lt;");
        assertThat(HtmlTextUtil.unescape("&amp;amp;")).isEqualTo("&amp;");
        assertThat(HtmlTextUtil.unescape("&amp;#39;")).isEqualTo("&#39;");
    }
}
