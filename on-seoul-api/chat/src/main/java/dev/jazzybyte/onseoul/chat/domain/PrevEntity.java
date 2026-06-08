package dev.jazzybyte.onseoul.chat.domain;

/**
 * 직전 assistant 메시지의 service_cards에서 추출한 멀티턴 참조 해소(carryover)용 엔티티.
 *
 * <p>사실 필드(상태/접수일/장소 등)는 절대 포함하지 않는다. AI가 서수 인덱스로 참조를 바인딩할 수
 * 있도록 식별자({@code serviceId})와 표시 라벨({@code label}=service_name)만 담는다.
 *
 * @param serviceId service_card의 service_id(항상 채워짐)
 * @param label     service_card의 service_name. null이면 빈 문자열("")로 정규화한다(카드 자리는 유지).
 */
public record PrevEntity(String serviceId, String label) {}
