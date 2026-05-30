package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import dev.jazzybyte.onseoul.notification.port.in.CreateSubscriptionUseCase.CreateSubscriptionCommand;
import dev.jazzybyte.onseoul.notification.port.in.UpdateSubscriptionUseCase.UpdateSubscriptionCommand;
import dev.jazzybyte.onseoul.notification.port.out.LoadSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.SubscriptionFilterParserPort;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.dao.DataIntegrityViolationException;

import java.time.Instant;
import java.util.Optional;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

class NotificationSubscriptionServiceTest {

    private LoadSubscriptionPort loadPort;
    private SaveSubscriptionPort savePort;
    private SubscriptionFilterParserPort filterParser;
    private NotificationSubscriptionService service;

    @BeforeEach
    void setUp() {
        loadPort = mock(LoadSubscriptionPort.class);
        savePort = mock(SaveSubscriptionPort.class);
        filterParser = mock(SubscriptionFilterParserPort.class);
        when(filterParser.parse(anyString())).thenReturn(SubscriptionFilter.empty());
        service = new NotificationSubscriptionService(loadPort, savePort, filterParser);
    }

    private static SubscriptionFilter statusFilter() {
        return new SubscriptionFilter(Set.of("RECEIVING"), null, null, null);
    }

    @Test
    @DisplayName("create() — 정상 입력(조건 1개 이상) 시 insert 호출 후 view 반환")
    void create_validInput_callsInsert() {
        CreateSubscriptionCommand cmd = new CreateSubscriptionCommand(
                statusFilter(), Set.of(NotificationChannel.EMAIL));
        NotificationSubscription saved = NotificationSubscription.ofPersistence(
                100L, 1L, "{}",
                Set.of(NotificationChannel.EMAIL), null, Instant.now());
        when(savePort.insert(any())).thenReturn(saved);

        SubscriptionView result = service.create(1L, cmd);

        assertThat(result.id()).isEqualTo(100L);
        verify(savePort).insert(any());
    }

    @Test
    @DisplayName("create() — 빈 채널 → INVALID_INPUT")
    void create_emptyChannels_throws400() {
        CreateSubscriptionCommand cmd = new CreateSubscriptionCommand(
                statusFilter(), Set.of());
        assertThatThrownBy(() -> service.create(1L, cmd))
                .isInstanceOf(OnSeoulApiException.class)
                .extracting("errorCode").isEqualTo(ErrorCode.INVALID_INPUT);
    }

    @Test
    @DisplayName("create() — 빈 필터(전체 변경 구독) → INVALID_INPUT (빈 구독 가드)")
    void create_emptyFilter_throws400() {
        CreateSubscriptionCommand cmd = new CreateSubscriptionCommand(
                SubscriptionFilter.empty(), Set.of(NotificationChannel.EMAIL));
        assertThatThrownBy(() -> service.create(1L, cmd))
                .isInstanceOf(OnSeoulApiException.class)
                .extracting("errorCode").isEqualTo(ErrorCode.INVALID_INPUT);
        verify(savePort, never()).insert(any());
    }

    @Test
    @DisplayName("create() — 키워드 4개(초과) → INVALID_INPUT")
    void create_tooManyKeywords_throws400() {
        SubscriptionFilter tooMany = new SubscriptionFilter(
                null, null, null, Set.of("a", "b", "c", "d"));
        CreateSubscriptionCommand cmd = new CreateSubscriptionCommand(
                tooMany, Set.of(NotificationChannel.EMAIL));
        assertThatThrownBy(() -> service.create(1L, cmd))
                .isInstanceOf(OnSeoulApiException.class)
                .extracting("errorCode").isEqualTo(ErrorCode.INVALID_INPUT);
        verify(savePort, never()).insert(any());
    }

    @Test
    @DisplayName("create() — 키워드 정확히 3개(MAX_KEYWORDS 경계) → 허용, insert 호출")
    void create_exactlyMaxKeywords_callsInsert() {
        SubscriptionFilter three = new SubscriptionFilter(
                null, null, null, Set.of("a", "b", "c"));
        CreateSubscriptionCommand cmd = new CreateSubscriptionCommand(
                three, Set.of(NotificationChannel.EMAIL));
        NotificationSubscription saved = NotificationSubscription.ofPersistence(
                102L, 1L, "{\"keywords\":[\"a\",\"b\",\"c\"]}",
                Set.of(NotificationChannel.EMAIL), null, Instant.now());
        when(savePort.insert(any())).thenReturn(saved);

        SubscriptionView result = service.create(1L, cmd);

        assertThat(result.id()).isEqualTo(102L);
        verify(savePort).insert(any());
    }

    @Test
    @DisplayName("update() — 빈 필터로 갱신 시도 → INVALID_INPUT (빈 구독 가드, 서비스 레이어)")
    void update_emptyFilter_throws400() {
        UpdateSubscriptionCommand cmd = new UpdateSubscriptionCommand(SubscriptionFilter.empty(), null);

        assertThatThrownBy(() -> service.update(1L, 10L, cmd))
                .isInstanceOf(OnSeoulApiException.class)
                .extracting("errorCode").isEqualTo(ErrorCode.INVALID_INPUT);
        verify(savePort, never()).updatePartial(any(), any(), any());
    }

    @Test
    @DisplayName("update() — 키워드 4개로 갱신 시도 → INVALID_INPUT (서비스 레이어 키워드 제한)")
    void update_tooManyKeywords_throws400() {
        SubscriptionFilter tooMany = new SubscriptionFilter(null, null, null, Set.of("a", "b", "c", "d"));
        UpdateSubscriptionCommand cmd = new UpdateSubscriptionCommand(tooMany, null);

        assertThatThrownBy(() -> service.update(1L, 10L, cmd))
                .isInstanceOf(OnSeoulApiException.class)
                .extracting("errorCode").isEqualTo(ErrorCode.INVALID_INPUT);
        verify(savePort, never()).updatePartial(any(), any(), any());
    }

    @Test
    @DisplayName("create() — 키워드만으로도 정상 생성")
    void create_keywordsOnly_callsInsert() {
        SubscriptionFilter kw = new SubscriptionFilter(null, null, null, Set.of("수영"));
        CreateSubscriptionCommand cmd = new CreateSubscriptionCommand(
                kw, Set.of(NotificationChannel.EMAIL));
        NotificationSubscription saved = NotificationSubscription.ofPersistence(
                101L, 1L, "{\"keywords\":[\"수영\"]}",
                Set.of(NotificationChannel.EMAIL), null, Instant.now());
        when(savePort.insert(any())).thenReturn(saved);

        SubscriptionView result = service.create(1L, cmd);

        assertThat(result.id()).isEqualTo(101L);
        verify(savePort).insert(any());
    }

    @Test
    @DisplayName("create() — 키워드 있고 keywordTargets 비면 serverDefaults(둘 다)로 정규화된다")
    void create_keywordsWithoutTargets_normalizedToServerDefaults() {
        SubscriptionFilter kw = new SubscriptionFilter(null, null, null, Set.of("수영"));
        CreateSubscriptionCommand cmd = new CreateSubscriptionCommand(
                kw, Set.of(NotificationChannel.EMAIL));
        NotificationSubscription saved = NotificationSubscription.ofPersistence(
                103L, 1L, "{\"keywords\":[\"수영\"]}",
                Set.of(NotificationChannel.EMAIL), null, Instant.now());
        when(savePort.insert(any())).thenReturn(saved);

        service.create(1L, cmd);

        org.mockito.ArgumentCaptor<NotificationSubscription> captor =
                org.mockito.ArgumentCaptor.forClass(NotificationSubscription.class);
        verify(savePort).insert(captor.capture());
        assertThat(captor.getValue().getParsedFilter().keywordTargets())
                .containsExactlyInAnyOrder(
                        dev.jazzybyte.onseoul.notification.domain.KeywordTarget.SERVICE_NAME,
                        dev.jazzybyte.onseoul.notification.domain.KeywordTarget.PLACE_NAME);
    }

    @Test
    @DisplayName("create() — 사용자가 고른 keywordTargets(부분집합)는 그대로 보존된다")
    void create_userSelectedTargets_preserved() {
        SubscriptionFilter kw = new SubscriptionFilter(null, null, null, Set.of("수영"),
                Set.of(dev.jazzybyte.onseoul.notification.domain.KeywordTarget.PLACE_NAME));
        CreateSubscriptionCommand cmd = new CreateSubscriptionCommand(
                kw, Set.of(NotificationChannel.EMAIL));
        NotificationSubscription saved = NotificationSubscription.ofPersistence(
                104L, 1L, "{\"keywords\":[\"수영\"]}",
                Set.of(NotificationChannel.EMAIL), null, Instant.now());
        when(savePort.insert(any())).thenReturn(saved);

        service.create(1L, cmd);

        org.mockito.ArgumentCaptor<NotificationSubscription> captor =
                org.mockito.ArgumentCaptor.forClass(NotificationSubscription.class);
        verify(savePort).insert(captor.capture());
        assertThat(captor.getValue().getParsedFilter().keywordTargets())
                .containsExactly(dev.jazzybyte.onseoul.notification.domain.KeywordTarget.PLACE_NAME);
    }

    @Test
    @DisplayName("create() — keywordTargets만 채우고 다른 조건 다 비면 빈 구독으로 거부(INVALID_INPUT)")
    void create_onlyKeywordTargets_throws400() {
        SubscriptionFilter onlyTargets = new SubscriptionFilter(null, null, null, null,
                Set.of(dev.jazzybyte.onseoul.notification.domain.KeywordTarget.SERVICE_NAME));
        CreateSubscriptionCommand cmd = new CreateSubscriptionCommand(
                onlyTargets, Set.of(NotificationChannel.EMAIL));

        assertThatThrownBy(() -> service.create(1L, cmd))
                .isInstanceOf(OnSeoulApiException.class)
                .extracting("errorCode").isEqualTo(ErrorCode.INVALID_INPUT);
        verify(savePort, never()).insert(any());
    }

    @Test
    @DisplayName("create() — DataIntegrityViolation → SUBSCRIPTION_CONFLICT")
    void create_duplicate_throwsConflict() {
        CreateSubscriptionCommand cmd = new CreateSubscriptionCommand(
                statusFilter(), Set.of(NotificationChannel.EMAIL));
        when(savePort.insert(any())).thenThrow(new DataIntegrityViolationException("constraint violation"));

        assertThatThrownBy(() -> service.create(1L, cmd))
                .isInstanceOf(OnSeoulApiException.class)
                .extracting("errorCode").isEqualTo(ErrorCode.SUBSCRIPTION_CONFLICT);
    }

    @Test
    @DisplayName("update() — 다른 사용자의 구독 → FORBIDDEN")
    void update_otherUserSubscription_throwsForbidden() {
        NotificationSubscription existing = NotificationSubscription.ofPersistence(
                10L, 2L, "{}",
                Set.of(NotificationChannel.EMAIL), null, Instant.now());
        when(loadPort.loadById(10L)).thenReturn(Optional.of(existing));

        UpdateSubscriptionCommand cmd = new UpdateSubscriptionCommand(statusFilter(), null);

        assertThatThrownBy(() -> service.update(1L, 10L, cmd))
                .isInstanceOf(OnSeoulApiException.class)
                .extracting("errorCode").isEqualTo(ErrorCode.FORBIDDEN);
        verify(savePort, never()).updatePartial(any(), any(), any());
    }

    @Test
    @DisplayName("update() — 미존재 → SUBSCRIPTION_NOT_FOUND")
    void update_notFound_throws404() {
        when(loadPort.loadById(99L)).thenReturn(Optional.empty());
        UpdateSubscriptionCommand cmd = new UpdateSubscriptionCommand(statusFilter(), null);

        assertThatThrownBy(() -> service.update(1L, 99L, cmd))
                .isInstanceOf(OnSeoulApiException.class)
                .extracting("errorCode").isEqualTo(ErrorCode.SUBSCRIPTION_NOT_FOUND);
    }

    @Test
    @DisplayName("update() — filter 만 있을 때 updatePartial 에 도메인 타입 그대로 전달")
    void update_filterOnly_callsUpdatePartialWithChannelsNull() {
        NotificationSubscription existing = NotificationSubscription.ofPersistence(
                10L, 1L, "{}",
                Set.of(NotificationChannel.EMAIL), null, Instant.now());
        when(loadPort.loadById(10L)).thenReturn(Optional.of(existing));
        NotificationSubscription updated = NotificationSubscription.ofPersistence(
                10L, 1L, "{\"statuses\":[\"RECEIVING\"]}",
                Set.of(NotificationChannel.EMAIL), null, Instant.now());
        SubscriptionFilter newFilter = new SubscriptionFilter(Set.of("RECEIVING"), null, null, null);
        when(savePort.updatePartial(eq(10L), eq(newFilter), eq(null))).thenReturn(updated);

        UpdateSubscriptionCommand cmd = new UpdateSubscriptionCommand(newFilter, null);

        SubscriptionView result = service.update(1L, 10L, cmd);
        assertThat(result.id()).isEqualTo(10L);
        verify(savePort).updatePartial(eq(10L), eq(newFilter), eq(null));
    }

    @Test
    @DisplayName("update() — filter/channels 모두 null → INVALID_INPUT (application 책임)")
    void update_bothNull_throws400() {
        UpdateSubscriptionCommand cmd = new UpdateSubscriptionCommand(null, null);

        assertThatThrownBy(() -> service.update(1L, 10L, cmd))
                .isInstanceOf(OnSeoulApiException.class)
                .extracting("errorCode").isEqualTo(ErrorCode.INVALID_INPUT);
        verifyNoInteractions(loadPort);
        verify(savePort, never()).updatePartial(any(), any(), any());
    }

    @Test
    @DisplayName("update() — 빈 channels → INVALID_INPUT")
    void update_emptyChannels_throws400() {
        NotificationSubscription existing = NotificationSubscription.ofPersistence(
                10L, 1L, "{}",
                Set.of(NotificationChannel.EMAIL), null, Instant.now());
        when(loadPort.loadById(10L)).thenReturn(Optional.of(existing));

        UpdateSubscriptionCommand cmd = new UpdateSubscriptionCommand(null, Set.of());

        assertThatThrownBy(() -> service.update(1L, 10L, cmd))
                .isInstanceOf(OnSeoulApiException.class)
                .extracting("errorCode").isEqualTo(ErrorCode.INVALID_INPUT);
        verify(savePort, never()).updatePartial(any(), any(), any());
    }

    @Test
    @DisplayName("delete() — 본인 구독 → deleteById 호출")
    void delete_ownSubscription_callsDelete() {
        NotificationSubscription existing = NotificationSubscription.ofPersistence(
                10L, 1L, "{}",
                Set.of(NotificationChannel.EMAIL), null, Instant.now());
        when(loadPort.loadById(10L)).thenReturn(Optional.of(existing));

        service.delete(1L, 10L);

        verify(savePort).deleteById(10L);
    }

    @Test
    @DisplayName("delete() — 다른 사용자 구독 → FORBIDDEN, deleteById 미호출")
    void delete_otherUser_throwsForbidden() {
        NotificationSubscription existing = NotificationSubscription.ofPersistence(
                10L, 2L, "{}",
                Set.of(NotificationChannel.EMAIL), null, Instant.now());
        when(loadPort.loadById(10L)).thenReturn(Optional.of(existing));

        assertThatThrownBy(() -> service.delete(1L, 10L))
                .isInstanceOf(OnSeoulApiException.class)
                .extracting("errorCode").isEqualTo(ErrorCode.FORBIDDEN);
        verify(savePort, never()).deleteById(any());
    }

    @Test
    @DisplayName("list() — loadByUserId 위임, filterParser.parse 호출 후 view 반환")
    void list_delegatesToPortAndParsesFilter() {
        NotificationSubscription sub = NotificationSubscription.ofPersistence(
                10L, 1L, "{}",
                Set.of(NotificationChannel.EMAIL), null, Instant.now());
        when(loadPort.loadByUserId(1L)).thenReturn(java.util.List.of(sub));

        java.util.List<SubscriptionView> result = service.list(1L);

        assertThat(result).hasSize(1);
        assertThat(result.get(0).id()).isEqualTo(10L);
        assertThat(result.get(0).filter()).isEqualTo(SubscriptionFilter.empty());
        verify(loadPort).loadByUserId(1L);
        verify(filterParser).parse("{}");
    }
}
