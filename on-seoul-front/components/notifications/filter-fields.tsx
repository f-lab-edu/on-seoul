"use client";

import { KeywordInput } from "@/components/notifications/keyword-input";
import {
  CATEGORY_OPTIONS,
  KEYWORD_TARGET_LABEL,
  SEOUL_DISTRICTS,
  STATUS_LABEL,
  type KeywordTarget,
  type ServiceStatus,
  type SubscriptionFilter,
} from "@/types/notification";

const STATUSES: ServiceStatus[] = ["RECEIVING", "STANDBY", "CLOSED"];
const KEYWORD_TARGETS: KeywordTarget[] = ["SERVICE_NAME", "PLACE_NAME"];

interface FilterFieldsProps {
  value: SubscriptionFilter;
  onChange: (next: SubscriptionFilter) => void;
}

/**
 * 최소 1개 조건 충족 여부 — statuses/areaNames/maxClassNames/keywords 중 하나라도
 * 비어있지 않으면 true. 명세 §4.2 / §8.
 */
export function hasAtLeastOneCondition(filter: SubscriptionFilter): boolean {
  return (
    (filter.statuses?.length ?? 0) > 0 ||
    (filter.areaNames?.length ?? 0) > 0 ||
    (filter.maxClassNames?.length ?? 0) > 0 ||
    (filter.keywords?.length ?? 0) > 0
  );
}

/**
 * 조건 요약 — 카드 헤드라인용. 명세 §3.1.
 * 지역 · 카테고리 · 상태 · 키워드 순서, ` · `로 연결.
 */
export function summarizeFilter(filter: SubscriptionFilter): string {
  const parts: string[] = [];
  if (filter.areaNames?.length) parts.push(filter.areaNames.join(", "));
  if (filter.maxClassNames?.length) parts.push(filter.maxClassNames.join(", "));
  if (filter.statuses?.length) {
    parts.push(filter.statuses.map((s) => STATUS_LABEL[s]).join(", "));
  }
  if (filter.keywords?.length) parts.push(`키워드: ${filter.keywords.join(", ")}`);
  // 정본 §3.1: 모든 조건이 비면 카드 자체가 생성될 수 없으나(최소 1개 강제),
  // 방어적으로 placeholder를 둔다.
  return parts.length > 0 ? parts.join(" · ") : "전체 조건";
}

/**
 * 구독 필터 편집 폼 섹션. 편집/생성 모달 공통.
 *
 * 정본: docs/2026-05-28-frontend-personalized-notification.md §3, §5.
 */
export function FilterFields({ value, onChange }: FilterFieldsProps) {
  function toggle<T extends string>(list: readonly T[] | undefined, item: T): T[] {
    const current = list ?? [];
    return current.includes(item)
      ? current.filter((v) => v !== item)
      : [...current, item];
  }

  const statuses = value.statuses ?? [];
  const areaNames = value.areaNames ?? [];
  const maxClassNames = value.maxClassNames ?? [];
  const keywords = value.keywords ?? [];
  const keywordTargets = value.keywordTargets ?? [];
  const hasKeywords = keywords.length > 0;

  return (
    <div className="flex flex-col gap-4">
      <fieldset className="flex flex-col gap-2">
        <legend className="text-sm font-medium">상태</legend>
        <div className="flex flex-wrap gap-2">
          {STATUSES.map((s) => {
            const id = `filter-status-${s}`;
            return (
              <div
                key={s}
                className="flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs"
              >
                <input
                  type="checkbox"
                  id={id}
                  checked={statuses.includes(s)}
                  onChange={() =>
                    onChange({ ...value, statuses: toggle(statuses, s) })
                  }
                />
                <label htmlFor={id}>{STATUS_LABEL[s]}</label>
              </div>
            );
          })}
        </div>
      </fieldset>

      <fieldset className="flex flex-col gap-2">
        <legend className="text-sm font-medium">지역</legend>
        <div className="grid max-h-40 grid-cols-3 gap-1 overflow-y-auto rounded-md border border-border p-2 text-xs">
          {SEOUL_DISTRICTS.map((d) => {
            const id = `filter-area-${d}`;
            return (
              <div key={d} className="flex items-center gap-1">
                <input
                  type="checkbox"
                  id={id}
                  checked={areaNames.includes(d)}
                  onChange={() =>
                    onChange({ ...value, areaNames: toggle(areaNames, d) })
                  }
                />
                <label htmlFor={id}>{d}</label>
              </div>
            );
          })}
        </div>
      </fieldset>

      <fieldset className="flex flex-col gap-2">
        <legend className="text-sm font-medium">카테고리</legend>
        <div className="flex flex-wrap gap-2">
          {CATEGORY_OPTIONS.map((c) => {
            const id = `filter-category-${c}`;
            return (
              <div
                key={c}
                className="flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs"
              >
                <input
                  type="checkbox"
                  id={id}
                  checked={maxClassNames.includes(c)}
                  onChange={() =>
                    onChange({
                      ...value,
                      maxClassNames: toggle(maxClassNames, c),
                    })
                  }
                />
                <label htmlFor={id}>{c}</label>
              </div>
            );
          })}
        </div>
      </fieldset>

      <fieldset className="flex flex-col gap-2">
        <legend className="text-sm font-medium">키워드</legend>
        <KeywordInput
          value={keywords}
          onChange={(next) => onChange({ ...value, keywords: next })}
        />
      </fieldset>

      <fieldset
        className={`flex flex-col gap-2 ${hasKeywords ? "" : "opacity-60"}`}
      >
        <legend className="text-sm font-medium">키워드 대상</legend>
        <div className="flex flex-wrap gap-2">
          {KEYWORD_TARGETS.map((t) => {
            const id = `filter-keyword-target-${t}`;
            return (
              <div
                key={t}
                className="flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs"
              >
                <input
                  type="checkbox"
                  id={id}
                  checked={keywordTargets.includes(t)}
                  disabled={!hasKeywords}
                  onChange={() =>
                    onChange({
                      ...value,
                      keywordTargets: toggle(keywordTargets, t),
                    })
                  }
                />
                <label htmlFor={id}>{KEYWORD_TARGET_LABEL[t]}</label>
              </div>
            );
          })}
        </div>
        {!hasKeywords ? (
          <p className="text-xs text-muted-foreground">
            키워드를 입력하면 매칭 대상이 됩니다.
          </p>
        ) : keywordTargets.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            미선택 시 서비스명·장소명 둘 다에 매칭됩니다.
          </p>
        ) : null}
      </fieldset>

      {!hasAtLeastOneCondition(value) && (
        <p className="rounded-md bg-muted/60 px-3 py-2 text-xs text-muted-foreground">
          최소 1개 조건을 선택하세요 (상태/지역/카테고리/키워드).
        </p>
      )}
    </div>
  );
}
