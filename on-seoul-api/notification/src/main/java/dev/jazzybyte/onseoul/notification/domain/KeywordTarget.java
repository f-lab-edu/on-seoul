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
 * <p>현재 사용자는 대상을 직접 선택하지 않는다 — 서버가 {@link #serverDefaults()} 전체에 매칭한다.
 */
public enum KeywordTarget {
    SERVICE_NAME,
    PLACE_NAME;

    /**
     * 서버가 키워드 매칭에 사용하는 기본 대상 컬럼 집합.
     * 현재는 모든 대상에 매칭한다. 대상 일부만 쓰고 싶으면 이 집합만 조정.
     */
    public static Set<KeywordTarget> serverDefaults() {
        return Set.of(SERVICE_NAME, PLACE_NAME);
    }
}
