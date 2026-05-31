/**
 * 개인화 알림 도메인 타입.
 *
 * 정본:
 * - `on-seoul-api` notification BC
 *   (`NotificationSubscriptionController`, `NotificationDispatchController`)
 * - 명세: `on-seoul-front/docs/2026-05-28-frontend-personalized-notification.md`
 *   - §2 도메인 용어, §4 API 계약, §5 필터 값 마스터 데이터
 *
 * v1 제약:
 * - `channels`는 항상 `["EMAIL"]`로 송수신한다.
 *   백엔드는 `SMS`를 도메인에 보존하므로 응답에 포함될 수 있으나, 프론트는 표시하지 않는다.
 *
 * 2026-05-28 변경:
 * - 구독은 더 이상 특정 서비스에 묶이지 않는다(§2). `serviceId`/`serviceName`
 *   필드와 `(user_id, service_id)` 중복 방지 제약이 제거되었다.
 * - 필터에 `keywords`(최대 3개)와 `keywordTargets`(SERVICE_NAME / PLACE_NAME)가
 *   추가되었다(§4.2, §5.4~5.5).
 * - statuses/areaNames/maxClassNames/keywords 중 최소 1개 필수(§4.2).
 */

/** 서비스 상태 (필터 옵션) — 백엔드 enum 미러. */
export type ServiceStatus = "RECEIVING" | "STANDBY" | "CLOSED";

/** 수신 채널. v1은 EMAIL 단독 사용. */
export type Channel = "EMAIL" | "SMS";

/**
 * 키워드를 비교할 컬럼(§5.5).
 * 대소문자 정확히 일치해야 한다.
 */
export type KeywordTarget = "SERVICE_NAME" | "PLACE_NAME";

/**
 * 구독 필터.
 * statuses/areaNames/maxClassNames/keywords 중 **최소 1개**는 비어있지 않아야 한다(§4.2).
 * 모두 비우면 백엔드가 400(`validation_error`)을 반환한다.
 */
export interface SubscriptionFilter {
  statuses?: ServiceStatus[];
  areaNames?: string[];
  maxClassNames?: string[];
  /** 부분일치(ILIKE) 검색어. 최대 3개, 키워드 간 OR(§5.4). */
  keywords?: string[];
  /** 키워드 비교 컬럼. 대상 간 OR. 미지정 시 백엔드가 둘 다 적용(§5.5). */
  keywordTargets?: KeywordTarget[];
}

/** 구독 1건. */
export interface Subscription {
  id: number;
  filter: SubscriptionFilter;
  channels: Channel[];
  /** 마지막 발송 시각. 발송 이력 없으면 null. */
  lastNotifiedAt: string | null;
  createdAt: string;
}

/** GET /api/notifications/subscriptions 응답. */
export interface SubscriptionsResponse {
  subscriptions: Subscription[];
}

/** POST /api/notifications/subscriptions 요청 본문(§4.2). */
export interface CreateSubscriptionRequest {
  filter: SubscriptionFilter;
  channels: Channel[];
}

/** PATCH /api/notifications/subscriptions/{id} 요청 본문(§4.3). */
export interface UpdateSubscriptionRequest {
  filter?: SubscriptionFilter;
  channels?: Channel[];
}

/** 발송 결과 상태. */
export type DispatchStatus = "SUCCESS" | "FAILED";

/** 발송 이력 1건. */
export interface Dispatch {
  id: number;
  subscriptionId: number;
  /** 백엔드 JOIN 미확정 — 없으면 fallback. */
  serviceName: string | null;
  title: string;
  body: string;
  status: DispatchStatus;
  sentAt: string;
}

/** GET /api/notifications/dispatches 응답. */
export interface DispatchesResponse {
  dispatches: Dispatch[];
  nextCursor: number | null;
}

/* ----- 마스터 상수 ----- */

/** 서비스 상태 한국어 라벨. 명세 §5.1. */
export const STATUS_LABEL: Record<ServiceStatus, string> = {
  RECEIVING: "접수중",
  STANDBY: "접수대기",
  CLOSED: "접수마감",
};

/** 키워드 대상 한국어 라벨. 명세 §5.5. */
export const KEYWORD_TARGET_LABEL: Record<KeywordTarget, string> = {
  SERVICE_NAME: "서비스명",
  PLACE_NAME: "장소명",
};

/** 구독당 최대 키워드 개수. 명세 §5.4. */
export const KEYWORD_MAX_COUNT = 3;

/** 카테고리 옵션 (`maxClassNames`). 명세 §5.2. */
export const CATEGORY_OPTIONS: readonly string[] = [
  "문화행사",
  "체육시설",
  "시설대관",
  "교육",
  "진료",
] as const;

/**
 * 서울시 25개 자치구.
 * `areaNames` 마스터 데이터 제공 방식 미확정 — 정적 하드코딩으로 우선 진행.
 * 백엔드 협업 체크리스트(§9) 결정 시 `/api/areas` 등으로 대체.
 */
export const SEOUL_DISTRICTS: readonly string[] = [
  "강남구",
  "강동구",
  "강북구",
  "강서구",
  "관악구",
  "광진구",
  "구로구",
  "금천구",
  "노원구",
  "도봉구",
  "동대문구",
  "동작구",
  "마포구",
  "서대문구",
  "서초구",
  "성동구",
  "성북구",
  "송파구",
  "양천구",
  "영등포구",
  "용산구",
  "은평구",
  "종로구",
  "중구",
  "중랑구",
] as const;
