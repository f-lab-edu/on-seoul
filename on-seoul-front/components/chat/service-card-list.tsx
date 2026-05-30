import { ServiceCardItem } from "@/components/chat/service-card-item";
import type { ServiceCard } from "@/types/sse-events";

interface ServiceCardListProps {
  cards: ServiceCard[];
}

/**
 * ASSISTANT 답변 버블 아래에 노출되는 시설 카드 목록.
 * 빈 배열이면 null 반환 — 카드 영역 자체를 그리지 않는다
 * (docs/chat-service-cards-interface.md §7 빈 결과 정책).
 */
export function ServiceCardList({ cards }: ServiceCardListProps) {
  if (cards.length === 0) return null;
  return (
    <div className="flex w-full max-w-[80%] flex-col gap-2">
      {cards.map((card) => (
        <ServiceCardItem key={card.service_id} card={card} />
      ))}
    </div>
  );
}
