"use client";

import {
  CATEGORY_OPTIONS,
  SEOUL_DISTRICTS,
  STATUS_LABEL,
  type ServiceStatus,
  type SubscriptionFilter,
} from "@/types/notification";

const STATUSES: ServiceStatus[] = ["RECEIVING", "STANDBY", "CLOSED"];

interface FilterFieldsProps {
  value: SubscriptionFilter;
  onChange: (next: SubscriptionFilter) => void;
}

/**
 * 구독 필터 편집 폼 섹션.
 * 편집/생성 모달에서 공통 사용.
 */
export function FilterFields({ value, onChange }: FilterFieldsProps) {
  function toggle<T extends string>(list: T[], item: T): T[] {
    return list.includes(item) ? list.filter((v) => v !== item) : [...list, item];
  }

  const isEmpty =
    value.statuses.length === 0 &&
    value.areaNames.length === 0 &&
    value.maxClassNames.length === 0;

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
                  checked={value.statuses.includes(s)}
                  onChange={() =>
                    onChange({ ...value, statuses: toggle(value.statuses, s) })
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
                  checked={value.areaNames.includes(d)}
                  onChange={() =>
                    onChange({ ...value, areaNames: toggle(value.areaNames, d) })
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
                  checked={value.maxClassNames.includes(c)}
                  onChange={() =>
                    onChange({
                      ...value,
                      maxClassNames: toggle(value.maxClassNames, c),
                    })
                  }
                />
                <label htmlFor={id}>{c}</label>
              </div>
            );
          })}
        </div>
      </fieldset>

      {isEmpty && (
        <p className="rounded-md bg-muted/60 px-3 py-2 text-xs text-muted-foreground">
          이 서비스의 모든 변경에 대해 알림을 받습니다.
        </p>
      )}
    </div>
  );
}
