import { ExternalLink } from "lucide-react";

import { formatDate } from "@/lib/format-date";
import { cn } from "@/lib/utils";
import type { ServiceCard } from "@/types/sse-events";

interface ServiceCardListProps {
  cards: ServiceCard[];
}

/**
 * ASSISTANT 답변 버블 아래에 노출되는 시설 표.
 * 빈 배열이면 null 반환 — 표 영역 자체를 그리지 않는다
 * (docs/chat-service-cards-interface.md §7 빈 결과 정책).
 *
 * 카드 대신 표 형태 — 메시지 버블 대비 과한 시각 면적을 줄이고 한 화면에 더 많은 결과를 노출한다.
 * 모바일에서는 좌우 스크롤로 표 전체를 볼 수 있다.
 */
export function ServiceCardList({ cards }: ServiceCardListProps) {
  if (cards.length === 0) return null;

  return (
    <div className="-mx-1 overflow-x-auto rounded-md border border-border">
      <table className="w-full min-w-[36rem] text-xs">
        <thead className="bg-muted/60 text-muted-foreground">
          <tr>
            <th scope="col" className="px-2 py-1.5 text-left font-medium">
              시설
            </th>
            <th scope="col" className="px-2 py-1.5 text-left font-medium">
              분류·위치
            </th>
            <th scope="col" className="px-2 py-1.5 text-left font-medium">
              상태
            </th>
            <th scope="col" className="px-2 py-1.5 text-left font-medium">
              접수기간
            </th>
            <th scope="col" className="px-2 py-1.5 text-right font-medium">
              <span className="sr-only">바로가기</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {cards.map((card) => (
            <ServiceCardRow key={card.service_id} card={card} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ServiceCardRow({ card }: { card: ServiceCard }) {
  const location = [card.area_name, card.place_name].filter(Boolean).join(" ");
  const category = [card.max_class_name, card.min_class_name]
    .filter(Boolean)
    .join(" > ");
  const meta = [location, category].filter(Boolean).join(" · ");

  const start = formatDate(card.receipt_start_dt);
  const end = formatDate(card.receipt_end_dt);
  const period =
    start && end ? `${start} ~ ${end}` : start ? `${start} ~` : end ? `~ ${end}` : "—";

  return (
    <tr className="align-top">
      <td className="px-2 py-1.5 font-medium break-keep">
        {card.service_name ?? "—"}
      </td>
      <td className="px-2 py-1.5 text-muted-foreground break-keep">
        {meta || "—"}
      </td>
      <td className="px-2 py-1.5 whitespace-nowrap">
        {card.service_status ? <StatusChip status={card.service_status} /> : "—"}
      </td>
      <td className="px-2 py-1.5 whitespace-nowrap text-muted-foreground">
        {period}
      </td>
      <td className="px-2 py-1.5 text-right">
        <a
          href={card.service_url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`${card.service_name ?? "예약 페이지"} 새 창에서 열기`}
          className="inline-flex h-8 w-8 items-center justify-center rounded-md text-primary hover:bg-muted"
        >
          <ExternalLink className="h-4 w-4" aria-hidden />
        </a>
      </td>
    </tr>
  );
}

/** service_status 한글 라벨을 색상 칩으로 매핑. 미지의 값은 gray 폴백. */
function StatusChip({ status }: { status: string }) {
  return (
    <span
      className={cn(
        "rounded-full px-1.5 py-0.5 text-[10px] font-medium whitespace-nowrap",
        statusClassName(status),
      )}
    >
      {status}
    </span>
  );
}

function statusClassName(status: string): string {
  switch (status) {
    case "접수중":
      return "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300";
    case "예약일시중지":
      return "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300";
    default:
      return "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300";
  }
}
