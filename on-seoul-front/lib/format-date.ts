/**
 * 백엔드가 ISO 8601 문자열로 직렬화하는 날짜 필드를 `YYYY-MM-DD` 로 절단.
 * 시간 부분이 의미 없는 데이터가 다수이므로 카드 UI 에서는 날짜만 노출한다.
 * (docs/chat-service-cards-interface.md §5)
 */
export function formatDate(iso: string | null): string {
  return iso ? iso.slice(0, 10) : "";
}
