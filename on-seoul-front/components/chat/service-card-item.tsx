import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDate } from "@/lib/format-date";
import { cn } from "@/lib/utils";
import type { ServiceCard } from "@/types/sse-events";

interface ServiceCardItemProps {
  card: ServiceCard;
}

/**
 * 개별 시설 카드. null 필드는 라인 자체를 생략한다
 * (docs/chat-service-cards-interface.md §2.2 — 빈 값 "—" 표시 금지).
 */
export function ServiceCardItem({ card }: ServiceCardItemProps) {
  const location = [card.area_name, card.place_name].filter(Boolean).join(" ");
  const category = [card.max_class_name, card.min_class_name].filter(Boolean).join(" > ");
  const meta = [
    card.payment_type ? `요금: ${card.payment_type}` : null,
    card.target_info ? `대상: ${card.target_info}` : null,
  ]
    .filter(Boolean)
    .join(" / ");
  const start = formatDate(card.receipt_start_dt);
  const end = formatDate(card.receipt_end_dt);
  // 한쪽만 null일 때 "접수:  ~ 2025-12-01" 같은 갭이 생기지 않도록 라벨을 분기.
  const period = start && end
    ? `접수: ${start} ~ ${end}`
    : start
      ? `접수 시작: ${start}`
      : end
        ? `접수 종료: ${end}`
        : "";

  return (
    <Card size="sm">
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-2">
          {card.service_name && (
            <CardTitle className="break-words">{card.service_name}</CardTitle>
          )}
          {card.service_status && <StatusChip status={card.service_status} />}
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-1 text-xs text-muted-foreground">
        {location && <p>{location}</p>}
        {category && <p>{category}</p>}
        {meta && <p>{meta}</p>}
        {period && <p>{period}</p>}
        <a
          href={card.service_url}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-1 inline-flex min-h-11 items-center self-start text-sm font-medium text-primary underline-offset-2 hover:underline"
        >
          바로가기
        </a>
      </CardContent>
    </Card>
  );
}

/** service_status 한글 라벨을 색상 칩으로 매핑. 미지의 값은 gray 폴백. */
function StatusChip({ status }: { status: string }) {
  return (
    <span
      className={cn(
        "rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap",
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
    case "예약마감":
    case "접수종료":
    case "안내중":
      return "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300";
    default:
      return "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300";
  }
}
