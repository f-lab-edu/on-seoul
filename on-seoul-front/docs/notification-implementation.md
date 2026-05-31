# 프론트엔드 — 개인화 알림 관리 구현 목록

Next.js 15 App Router 기반 알림 관리 화면 구현 순서.
각 Phase는 독립적으로 동작 확인 후 다음 단계로 진행한다.

> **v1 제약**: Knock 무료 등급으로 **SMS 발송 불가**. 채널은 EMAIL 전용으로 노출하고 SMS 관련 UI는 모두 숨김 처리한다.
> Knock 유료 전환 시 별도 Phase로 SMS 활성화.

> **선행 조건**: 백엔드 협업 체크리스트의 미완료 항목 확인 후 진행.
> - [ ] 서비스 검색 API 경로 확정
> - [ ] `areaNames` 마스터 데이터 제공 방식 결정 (API vs 정적)
> - [ ] 발송 이력 응답에 `serviceName` 포함 여부 (JOIN 필요)

상위 규약은 [`CLAUDE.md`](../CLAUDE.md), 알림 도메인 설계는 [`docs/2026-05-28-frontend-personalized-notification.md`](./2026-05-28-frontend-personalized-notification.md) 참조.

---

## Epic 6 — `feat/notifications`
> Phase 16–20 | 개인화 알림 구독 관리 + 발송 이력

### Phase 16. 타입 정의

- [ ] `types/notification.ts` 신규 생성 (출처: `on-seoul-api` notification BC)
  - `SubscriptionFilter` — `statuses`, `areaNames`, `maxClassNames` 배열
  - `Subscription` — `id`, `serviceId`, `serviceName`, `filter`, `channels`, `lastNotifiedAt`, `createdAt`
  - `Channel` — `"EMAIL" | "SMS"` (v1은 `"EMAIL"` 단독 사용)
  - `Dispatch` — `id`, `subscriptionId`, `serviceName`, `title`, `body`, `status`, `sentAt`
  - `DispatchStatus` — `"SUCCESS" | "FAILED"`
  - `ServiceStatus` — `"RECEIVING" | "STANDBY" | "CLOSED"` (필터 옵션)
- [ ] `STATUS_LABEL`, `CATEGORY_OPTIONS`, `SEOUL_DISTRICTS` 상수 정의 (마스터 데이터)
  - `areaNames` 제공 방식 미확정 시 → 서울 25개 자치구 정적 하드코딩으로 우선 진행

검증: `pnpm typecheck` 통과

### Phase 17. API 훅 (TanStack Query)

- [ ] `hooks/useSubscriptions.ts`
  - `useSubscriptions()` — `GET /api/notifications/subscriptions` 조회 (`staleTime: 30_000`)
  - `useCreateSubscription()` — `POST /api/notifications/subscriptions` mutation + 목록 invalidate
  - `useUpdateSubscription(id)` — `PATCH /api/notifications/subscriptions/{id}` mutation + 낙관적 UI
  - `useDeleteSubscription(id)` — `DELETE /api/notifications/subscriptions/{id}` mutation + 목록 invalidate
- [ ] `hooks/useDispatches.ts`
  - `useDispatches()` — `GET /api/notifications/dispatches?cursor=&size=20` 무한 스크롤 (`useInfiniteQuery`)
- [ ] 에러 응답 처리 — `409 Conflict` → "이미 구독 중인 서비스입니다" / `403` → "권한이 없습니다" 토스트
- [ ] `channels` 페이로드는 항상 `["EMAIL"]` 고정 (v1 제약 — SMS 포함 금지)

검증: `pnpm typecheck && pnpm lint` 통과

### Phase 18. 알림 설정 메인 (`/settings/notifications`)

- [ ] `app/(chat)/settings/notifications/page.tsx` — 서버 컴포넌트 (인증 가드는 상위 layout 위임)
- [ ] `components/notifications/subscription-list.tsx` — 구독 카드 목록 (클라이언트)
- [ ] `components/notifications/subscription-card.tsx` — 구독 1건 카드
  - 서비스 이름, 필터 요약 (예: "강남구 · 문화행사 · 접수중"), EMAIL 뱃지
  - `lastNotifiedAt` — 상대시간 + 절대시간 툴팁 (없으면 "발송 이력 없음")
  - **편집** 버튼 → 구독 편집 모달 오픈 / **해지** 버튼 → confirm dialog 후 DELETE
- [ ] 우상단 **새 구독 추가** 버튼 → 새 구독 모달 오픈
- [ ] 빈 상태 — "구독 중인 알림이 없습니다. 새 구독을 추가해보세요."
- [ ] 로딩 — `Skeleton` 카드 3개

검증: 목록 조회 → 카드 렌더 → 해지 confirm → DELETE 호출 후 목록 갱신

### Phase 19. 구독 편집 모달 + 새 구독 추가

**구독 편집 모달** (`components/notifications/subscription-edit-dialog.tsx`)

- [ ] shadcn `Dialog` 기반
- [ ] **필터 섹션**
  - 상태(`statuses`): `RECEIVING` / `STANDBY` / `CLOSED` 다중 선택 체크박스 + 한국어 레이블
  - 지역(`areaNames`): 서울시 25개 자치구 다중 선택 (Combobox 또는 체크박스 그룹)
  - 카테고리(`maxClassNames`): `문화행사` / `체육시설` / `시설대관` / `교육` / `진료` 다중 선택
  - 필터 전체 비움 → "이 서비스의 모든 변경에 대해 알림을 받습니다" 안내 텍스트 표시
- [ ] **채널 섹션 (v1)**
  - "이메일로 받기" 고정 표시 (토글 없음)
  - SMS 관련 UI 비공개 — 컴포넌트에 포함하지 않음
- [ ] **저장** — `PATCH` 호출 + 낙관적 UI (실패 시 롤백 + 토스트) / **취소** 버튼
- [ ] 폼 유효성: `channels` 비어 있으면 저장 차단 (v1은 항상 EMAIL이므로 사실상 불필요, 방어적 처리)

**새 구독 추가** (`components/notifications/subscription-create-dialog.tsx`)

- [ ] shadcn `Dialog` 기반
- [ ] 서비스 검색창 — API 경로 확정 시 `GET /api/...` 연동, 미확정 시 `serviceId` 직접 입력 임시 처리
- [ ] 검색 결과 선택 → 필터 편집 화면 (편집 모달과 동일한 필터 섹션 재사용)
- [ ] **저장** — `POST /api/notifications/subscriptions` 호출
  - `409 Conflict` → "이미 구독 중인 서비스입니다" 인라인 에러

검증: 편집 저장 → 낙관적 반영 → 성공 시 확정 / 실패 시 롤백 확인

### Phase 20. 발송 이력 (`/settings/notifications/history`)

- [ ] `app/(chat)/settings/notifications/history/page.tsx`
- [ ] `components/notifications/dispatch-list.tsx` — 무한 스크롤 목록 (클라이언트)
- [ ] `components/notifications/dispatch-item.tsx` — 이력 1건
  - 발송 시각(`sentAt`) — 상대시간 + 절대시간 툴팁
  - 서비스 이름 (`serviceName` 백엔드 미포함 시 → `subscriptionId`로 별도 조회 또는 "서비스 정보 없음" fallback)
  - 제목(`title`) / 본문 요약(`body` 첫 2줄)
  - 상태 뱃지 — `SUCCESS` (green) / `FAILED` (red)
  - 채널: v1은 EMAIL만 표시, SMS 이력이 응답에 포함되어도 렌더하지 않음
- [ ] `useInfiniteQuery` 커서 기반 페이지네이션 — 스크롤 하단 도달 시 `fetchNextPage`
- [ ] 빈 상태 — "발송된 알림 이력이 없습니다."

검증: 이력 목록 → 무한 스크롤 → `nextCursor` 소진 시 더 이상 요청 없음

---

## 주요 설계 준수 사항

1. **v1 채널 고정**: 모든 구독 생성/수정 요청의 `channels`는 `["EMAIL"]`. SMS 관련 UI·로직은 코드에 포함하지 않는다.
2. **낙관적 UI**: 필터·채널 변경은 UI에 즉시 반영, 실패 시 TanStack Query `onError` 콜백에서 롤백 + 토스트.
3. **에러 표준화**: 백엔드 `{ error, message }` 응답을 `api-client.ts`가 `ApiError`로 변환. 컴포넌트는 `ApiError.status`로 분기.
4. **인증 가드**: `/settings/**`는 `app/(chat)/layout.tsx`의 서버 사이드 가드가 커버. 별도 클라이언트 가드 불필요.
5. **shadcn 보존**: 편집 모달은 `Dialog` wrapper 패턴 사용. `components/ui/dialog.tsx` 직접 수정 금지.

---

## v2 (Knock 유료 전환 후)

- SMS 토글 복원 (`channels` 다중 선택)
- 전화번호 등록 화면 노출 (`/settings/notifications/contact`)
- 발송 이력 채널 컬럼 표시 (EMAIL / SMS 구분)
- "마지막 채널 보호" 가드 — 최소 1개 채널 필수 (프론트 + 백엔드 3중 방어 확인)
- `PATCH /api/users/me/contact` 연동 (`{ phoneNumber: "+821012345678" }`)
