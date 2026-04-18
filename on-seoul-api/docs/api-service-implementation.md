# API 서비스 구현 목록

Spring Boot + Gradle 기반 API 서비스 및 데이터 수집/적재 파이프라인 구현 순서.
각 Phase는 독립적으로 동작 확인 후 다음 단계로 진행한다.

브랜치는 아래 Epic 단위로 생성한다. Phase는 각 Epic 안의 작업 체크리스트다.

---

## Epic 1 — `feat/data-foundation`
> Phase 1–2 | DB / 계정 / 확장 준비, 수집 메타 테이블 생성까지 완료

### Phase 1. DB 기초 준비

- [x] PostgreSQL 설정에 `include = '/etc/postgresql/postgresql.custom.conf'` 추가
- [x] Database 생성 — `on_data`, `on_ai`
- [x] EXTENSION 설치 (컨테이너 이미지 `postgres` → `pgvector`로 변경)
- [x] DB 계정 및 권한 준비 — `on_data_app`, `on_ai_app`, `on_ai_reader`

검증: 각 계정으로 접속 후 대상 DB에 대해 권한 동작 확인

### Phase 2. 수집 메타 테이블 구성

- [x] 수집 관련 마이그레이션 스크립트 생성
- [x] 수집 이력 테이블 생성 — `on_data.collection_history`
  - 데이터셋 ID, 시작/종료 시각, 상태, 신규/변경/유지 건수, 에러 메시지
- [x] 수집 대상(데이터셋) 테이블 생성 — `on_data.scrap_target_dataset`
  - 데이터셋 ID(OA-2266 ~ OA-2270), 이름, 활성 여부, 최종 수집 시각

검증: 마이그레이션 실행 후 5개 데이터셋 초기 레코드 존재 확인

---

## Epic 2 — `feat/collection-pipeline`
> Phase 3–8 | 서울 열린데이터 광장 5개 API에 대한 수집 파이프라인 동작 확인

수집 흐름: 수집이력 생성 → Open API 호출(페이지네이션 전체 수집) → 공통 RDB 스키마 변환 → 기존 데이터 조회 → 신규/변경/유지 분류(`service_id` 기준) → DB Upsert → 수집이력 결과 기록

의존 관계: `T1 → T2 → T3 → T4 → T5`, `T1 → T6`, T7(테스트)은 각 태스크와 병행

### Phase 3. DB 스키마 및 엔티티 설계 (T1)

- [x] `public_service_reservations` 테이블 DDL 작성 (계획서 RDB 스키마 기준)
- [x] `collection_history` 테이블 설계 — 수집 일시, API명, 수집 건수, 신규/변경/유지 건수, 성공/실패 상태, 에러 메시지
- [x] JPA Entity + Repository 작성
- [x] 인덱스/제약 — `service_id` UNIQUE, `service_status`, `area_name`, `receipt_end_dt` 등 계획서 명시 인덱스 설정

### Phase 4. 서울시 Open API 클라이언트 구현 (T2)

- [x] 5개 API(OA-2266 ~ OA-2270) 공통 호출 클라이언트 (WebClient 기반)
- [x] 응답 DTO(24개 필드) 매핑
- [x] 페이지네이션 — `START_INDEX` / `END_INDEX` 반복 호출 (200건씩, 최대 1000건)
- [x] API 키 외부화 (`application.yml`)
- [x] 재시도 정책 — 최대 3회, 지수 백오프

### Phase 5. 데이터 변환기 구현 (T3)

- [x] API 응답 DTO → `PublicServiceReservation` 엔티티 매퍼
- [x] 날짜 문자열 → `LocalDateTime` 파싱 (서울시 API 포맷 대응)
- [x] X/Y 좌표 null 체크 및 기본값 처리 (Phase 8 Geocoding fallback 연계)
- [x] DTLCONT 원문 저장 (HTML 정제는 AI 벡터화 시점에 처리)
- [x] 필수 필드 검증 — `service_id`(Upsert 키), `service_name`(NOT NULL) null 시 해당 row 스킵 + ERROR 로그. 반환 타입 `Optional<PublicServiceReservation>`으로 변경

> 필드 구분: 필수(null이면 row 스킵 + ERROR) / 선택(null 허용 + WARN). 최소 `service_id`, `service_name`은 필수 처리

### Phase 6. 변경 감지 및 Upsert 로직 (T4)

- [ ] `service_id` 기준 기존 데이터 조회
- [ ] 신규 / 변경 / 유지 3분류
  - 신규: DB 미존재 → INSERT
  - 변경: 존재하지만 핵심 필드(`service_status`, `receipt_start_dt`, `receipt_end_dt`) 상이 → UPDATE + `prev_service_status` 갱신
  - 유지: 동일 → SKIP
- [ ] Upsert 결과(신규/변경/유지 건수) `collection_history` 기록

> 변경 판정 기준: DTLCONT 등 대형 텍스트 비교 비효율로 핵심 필드 비교 방식 채택

### Phase 7. 스케줄러 및 파이프라인 오케스트레이션 (T5)

- [ ] `@Scheduled(cron = "0 0 3 * * *")` — 매일 새벽 3시 실행
- [ ] 실행 순서: 수집이력 생성 → 5개 API 순차 호출 → 변환 → Upsert → 이력 완료 기록
- [ ] 예외 처리 — 한 API 실패 시 이력에 실패/에러 메시지 기록 후 다음 API 계속 진행
- [ ] 수동 트리거 엔드포인트 — `POST /admin/collection/trigger` (개발/테스트용)

### Phase 8. 좌표 누락 보정 — Geocoding fallback (T6)

- [ ] X/Y null 레코드에 대해 `PLACENM` 기반 카카오 Geocoding API 호출
- [ ] Upsert 후처리 또는 별도 배치로 실행
- [ ] Geocoding 결과 캐싱 (동일 장소명 반복 호출 방지)

> 카카오 Geocoding 한도 일 30만 건, 본 규모(1000건 미만)에서는 여유

### Phase 9. 수집 파이프라인 단위 테스트 (T7, 병행)

- [ ] T2 API 클라이언트 — 응답 Mock(`@WebClientTest` 또는 WireMock)으로 페이지네이션 / 에러 응답 / 타임아웃 검증
- [ ] T3 변환기 — 날짜 포맷 예외, 좌표 null, 필드 누락 엣지 케이스
- [ ] T4 Upsert — 신규/변경/유지 분류 정확성, `prev_service_status` 갱신
- [ ] T5 파이프라인 — 부분 실패 시 나머지 API 진행, 이력 기록 정확성

검증: 수동 트리거로 1회 수집 실행 → `collection_history` 및 `public_service_reservations` 레코드 확인

---

## Epic 3 — `feat/vector-schema`
> Phase 10 | AI 서비스가 사용할 벡터 스키마/인덱스 준비

> 메타데이터 임베딩 적재 및 하이브리드 검색 쿼리 자체는 AI 서비스(`on-seoul-agent`)가 책임지며 상세 항목은 `on-seoul-agent/docs/ai-service-implementation.md` 참조

### Phase 10. 벡터 스키마 구성

- [ ] PostgreSQL `vector` 스키마 생성
- [ ] `documents` 테이블 — `id`, `facility_id`(=`service_id`), `content`, `embedding vector`, `tsvector` 컬럼
- [ ] 인덱스 — pgvector(ivfflat 또는 hnsw) + GIN(tsvector)
- [ ] `on_ai_app` / `on_ai_reader` 권한 부여

검증: AI 서비스가 `embed_metadata.py`로 적재 및 조회 가능한지 연동 확인

---

## Epic 4 — `feat/api-foundation`
> Phase 11 | Spring Boot 앱 기동, 헬스체크 통과

### Phase 11. 프로젝트 구조 셋업

- [ ] Gradle Wrapper 구성 — `build.gradle.kts`, `settings.gradle.kts`
- [ ] 의존성 정의 — Spring Web, Security, Data JPA, Redis, PostgreSQL Driver
- [ ] 패키지 레이아웃 — `controller/`, `service/`, `domain/`, `repository/`, `scheduler/`, `security/`
- [ ] `application.yml` — 프로파일별 DB / Redis 접속 정보
- [ ] 헬스체크 엔드포인트

검증: `./gradlew bootRun` 실행 후 `/actuator/health` 200 응답

---

## Epic 5 — `feat/auth`
> Phase 12–13 | 세션 기반 인증, 회원가입/로그인/로그아웃 동작 확인

### Phase 12. 스키마 및 도메인

- [ ] PostgreSQL `app` 스키마 구성 — `users`, `query_history`
- [ ] JPA 엔티티 및 Repository
- [ ] Redis 세션 스토어 연결 (`spring-session-data-redis`)

### Phase 13. 인증 엔드포인트

- [ ] Spring Security Form Login (세션 기반)
- [ ] `POST /auth/signup` — 회원가입
- [ ] `POST /auth/login` — 로그인 (세션 발급)
- [ ] `POST /auth/logout` — 로그아웃 (세션 만료)
- [ ] 인증 예외 처리 (`@ControllerAdvice`)

검증: Postman으로 회원가입 → 로그인 → 보호 리소스 접근 → 로그아웃 플로우 확인

---

## Epic 6 — `feat/query-endpoint`
> Phase 14 | `/query` 위임 및 이력 저장 E2E 동작 확인

### Phase 14. 질의 엔드포인트

- [ ] `POST /query` — 인증된 사용자 질의 수신
- [ ] AI 서비스 `/chat/stream` 호출 및 SSE 릴레이
- [ ] `query_history` 이력 저장 (요청 시각, 사용자, 질의, 응답 요약)
- [ ] 에러 / 타임아웃 / 재시도 정책

검증: 로그인 세션으로 `/query` 호출 → 자연어 응답 수신 + `query_history` 레코드 확인

---

## Epic 7 — `feat/api-polish`
> Phase 15 | 테스트 통과

### Phase 15. 테스트

- [ ] 인증 / 세션 단위 테스트
- [ ] 예외 처리 테스트
- [ ] `/query` 통합 테스트 (AI 서비스 모킹)
- [ ] `./gradlew test` 전 구간 통과

---

## 참고

- DB는 단일 PostgreSQL 인스턴스(pgvector 포함). 스키마로 용도 분리 (`on_data`, `app`, `vector`)
- AI 서비스와의 연동은 내부 HTTP(SSE). 상세 흐름은 `docs/architecture.md` 참고
- 수집 스케줄러는 API 서비스가 담당, 상태 변경 감지 시 AI 서비스 `POST /notification/template` 호출로 알림 메시지 생성 위임
