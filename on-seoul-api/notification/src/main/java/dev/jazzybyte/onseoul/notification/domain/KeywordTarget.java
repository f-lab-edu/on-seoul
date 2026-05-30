package dev.jazzybyte.onseoul.notification.domain;

import java.util.Set;

/**
 * 키워드 부분일치(ILIKE) 매칭 대상 컬럼을 식별하는 도메인 enum.
 *
 * <p>도메인은 "어떤 의미의 컬럼을 매칭하는가"만 정의하고, 실제 jOOQ 컬럼 매핑은
 * 어댑터({@code ServiceChangePersistenceAdapter})가 담당한다 — 헥사고날 경계 유지.
 *
 * <p>확장 지점: 새 대상 컬럼을 추가하려면
 * <ol>
 *   <li>여기에 enum 값 1개 추가</li>
 *   <li>어댑터의 enum→컬럼 매핑(switch)에 분기 1개 추가</li>
 * </ol>
 * 만 하면 된다. API/DTO/필터 모델은 변경되지 않는다.
 *
 * <p>사용자는 구독별로 매칭 대상을 직접 선택할 수 있다
 * ({@link SubscriptionFilter#keywordTargets()}). 대상을 지정하지 않으면
 * {@link #serverDefaults()}(둘 다)로 정규화된다 — 기존 동작 보존 + 하위호환 fallback.
 */
public enum KeywordTarget {
    SERVICE_NAME,
    PLACE_NAME;

    /**
     * 기본 대상 컬럼 프리셋 겸 하위호환 fallback.
     *
     * <p>사용자가 대상을 고르지 않았거나(빈 keywordTargets) 구버전 JSONB 에 키가
     * 없을 때 이 집합(모든 대상)으로 정규화한다 — 기존 "둘 다 매칭" 동작을 보존한다.
     */
    public static Set<KeywordTarget> serverDefaults() {
        return Set.of(SERVICE_NAME, PLACE_NAME);
    }
}
