package dev.jazzybyte.onseoul.user.adapter.in.security;

import dev.jazzybyte.onseoul.exception.EmailConflictException;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import dev.jazzybyte.onseoul.user.port.in.SocialLoginUseCase;
import dev.jazzybyte.onseoul.user.port.in.TokenResponse;
import dev.jazzybyte.onseoul.user.port.out.TokenIssuerPort;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.dao.DataAccessException;
import org.springframework.dao.QueryTimeoutException;
import org.springframework.security.oauth2.client.authentication.OAuth2AuthenticationToken;
import org.springframework.security.oauth2.core.user.OAuth2User;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * [코드리뷰 MUST-FIX #2 검증] OAuth2LoginSuccessHandler가 모든 결과를
 * UriComponentsBuilder 기반 콜백 URL로 리다이렉트하고,
 * EmailConflictException의 existingProvider(구조적 필드)를 provider 쿼리로 전파하되
 * null이면 생략하는지 검증한다.
 *
 * <p>핸들러는 종전까지 직접 테스트가 없어 errorRedirect/callbackUrl 분기가
 * 미커버 상태였다. 본 테스트가 그 갭을 메운다.</p>
 */
@ExtendWith(MockitoExtension.class)
class OAuth2LoginSuccessHandlerRedirectTest {

    private static final String FRONT = "https://app.example.com";

    @Mock private SocialLoginUseCase socialLoginUseCase;
    @Mock private TokenIssuerPort tokenIssuerPort;
    @Mock private OAuth2AuthenticationToken authentication;
    @Mock private OAuth2User oauth2User;
    @Mock private HttpServletRequest request;
    @Mock private HttpServletResponse response;

    private OAuth2LoginSuccessHandler handler;

    @BeforeEach
    void setUp() {
        when(tokenIssuerPort.getAccessTokenMinutes()).thenReturn(30L);
        when(tokenIssuerPort.getRefreshTokenMinutes()).thenReturn(10080L);
        handler = new OAuth2LoginSuccessHandler(
                socialLoginUseCase, tokenIssuerPort, FRONT,
                /*cookieSecure*/ true, /*sameSite*/ "Strict", /*domain*/ "");
    }

    /** google principal 기본 셋업: sub, email, name 속성 제공. */
    private void stubGoogleUser(String sub) {
        when(authentication.getPrincipal()).thenReturn(oauth2User);
        when(authentication.getAuthorizedClientRegistrationId()).thenReturn("google");
        lenient().when(oauth2User.getAttribute("sub")).thenReturn(sub);
        lenient().when(oauth2User.getAttribute("email")).thenReturn("user@example.com");
        lenient().when(oauth2User.getAttribute("name")).thenReturn("홍길동");
    }

    private String captureRedirect() throws Exception {
        ArgumentCaptor<String> url = ArgumentCaptor.forClass(String.class);
        verify(response).sendRedirect(url.capture());
        return url.getValue();
    }

    @Test
    @DisplayName("성공 — status=success 콜백으로 리다이렉트하고 쿠키 2개를 심는다")
    void success_redirectsToStatusSuccess() throws Exception {
        stubGoogleUser("google-sub-1");
        when(socialLoginUseCase.socialLogin(any()))
                .thenReturn(new TokenResponse(1L, "at", "rt"));

        handler.onAuthenticationSuccess(request, response, authentication);

        assertThat(captureRedirect())
                .isEqualTo(FRONT + "/oauth/callback?status=success");
        verify(response, org.mockito.Mockito.times(2)).addHeader(org.mockito.ArgumentMatchers.eq("Set-Cookie"), any());
    }

    @Test
    @DisplayName("providerId 누락 — error=server_error 콜백으로 리다이렉트한다")
    void missingProviderId_redirectsToServerError() throws Exception {
        when(authentication.getPrincipal()).thenReturn(oauth2User);
        when(authentication.getAuthorizedClientRegistrationId()).thenReturn("google");
        when(oauth2User.getAttribute("sub")).thenReturn(null);
        when(oauth2User.getAttribute("id")).thenReturn(null);

        handler.onAuthenticationSuccess(request, response, authentication);

        assertThat(captureRedirect())
                .isEqualTo(FRONT + "/oauth/callback?error=server_error");
    }

    @Test
    @DisplayName("이메일 충돌(기존 벤더 known) — error=email_conflict&provider=google 으로 리다이렉트한다")
    void emailConflictWithProvider_redirectsWithProviderParam() throws Exception {
        stubGoogleUser("kakao-sub-conflict");
        when(socialLoginUseCase.socialLogin(any()))
                .thenThrow(new EmailConflictException("google", "이미 google(으)로 가입된 이메일입니다."));

        handler.onAuthenticationSuccess(request, response, authentication);

        assertThat(captureRedirect())
                .isEqualTo(FRONT + "/oauth/callback?error=email_conflict&provider=google");
    }

    @Test
    @DisplayName("이메일 충돌(경쟁 조건, provider=null) — provider 파라미터를 생략한다")
    void emailConflictWithoutProvider_omitsProviderParam() throws Exception {
        stubGoogleUser("kakao-sub-race");
        when(socialLoginUseCase.socialLogin(any()))
                .thenThrow(new EmailConflictException(null, "이미 가입된 이메일입니다."));

        handler.onAuthenticationSuccess(request, response, authentication);

        String redirect = captureRedirect();
        assertThat(redirect).isEqualTo(FRONT + "/oauth/callback?error=email_conflict");
        assertThat(redirect).doesNotContain("provider");
    }

    @Test
    @DisplayName("FORBIDDEN — error=forbidden 으로 리다이렉트한다")
    void forbidden_redirectsToForbidden() throws Exception {
        stubGoogleUser("google-suspended");
        when(socialLoginUseCase.socialLogin(any()))
                .thenThrow(new OnSeoulApiException(ErrorCode.FORBIDDEN, "비활성화된 계정"));

        handler.onAuthenticationSuccess(request, response, authentication);

        assertThat(captureRedirect())
                .isEqualTo(FRONT + "/oauth/callback?error=forbidden");
    }

    @Test
    @DisplayName("기타 OnSeoulApiException — error=server_error 로 리다이렉트한다")
    void otherApiException_redirectsToServerError() throws Exception {
        stubGoogleUser("google-other");
        when(socialLoginUseCase.socialLogin(any()))
                .thenThrow(new OnSeoulApiException(ErrorCode.SERVER_ERROR, "boom"));

        handler.onAuthenticationSuccess(request, response, authentication);

        assertThat(captureRedirect())
                .isEqualTo(FRONT + "/oauth/callback?error=server_error");
    }

    @Test
    @DisplayName("DataAccessException(예: Redis timeout) — error=server_error 로 리다이렉트한다(500으로 죽지 않음)")
    void dataAccessException_redirectsToServerError() throws Exception {
        stubGoogleUser("google-redis-down");
        when(socialLoginUseCase.socialLogin(any()))
                .thenThrow(new QueryTimeoutException("redis down"));

        handler.onAuthenticationSuccess(request, response, authentication);

        assertThat(captureRedirect())
                .isEqualTo(FRONT + "/oauth/callback?error=server_error");
    }

    @Test
    @DisplayName("리다이렉트 URL은 UriComponentsBuilder로 안전 인코딩된다 — provider 특수문자 미주입")
    void redirectUrl_isProperlyEncoded() throws Exception {
        stubGoogleUser("kakao-encode");
        // 비정상적이지만 인코딩 안전성 검증용: 공백/특수문자가 들어와도 쿼리 인젝션 없이 인코딩돼야 한다.
        when(socialLoginUseCase.socialLogin(any()))
                .thenThrow(new EmailConflictException("goo gle&x=1", "충돌"));

        handler.onAuthenticationSuccess(request, response, authentication);

        String redirect = captureRedirect();
        // error 파라미터는 그대로, provider 값은 인코딩(공백→%20, &→%26)돼 추가 파라미터로 새지 않아야 한다.
        assertThat(redirect).startsWith(FRONT + "/oauth/callback?error=email_conflict&provider=");
        assertThat(redirect).contains("goo%20gle%26x%3D1");
        // 인젝션된 'x=1'이 독립 쿼리 파라미터로 분리되면 안 된다.
        assertThat(redirect).doesNotContain("&x=1");
    }
}
