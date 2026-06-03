# on-seoul-api

on-seoul 프로젝트의 API 서비스입니다. 인증, 데이터 수집 스케줄링, 알림 발송, 대화 이력 관리를 담당합니다.

---

## 주요 역할

| 역할 | 설명                                                                             |
|---|--------------------------------------------------------------------------------|
| 데이터 수집 | 서울 열린데이터 광장 5개 API에서 공공서비스 예약 데이터를 주기적으로 수집하고 변경 감지                            |
| 인증 | OAuth 2.0 소셜 로그인 + JWT (Access Token / Refresh Token)                          |
| 챗봇 릴레이 | 사용자 질의를 AI 서비스(`on-seoul-agent`)에 위임하고 SSE 응답을 프론트엔드에 릴레이                      |
| 알림 발송 | 알림 스케줄러가 구독 조건별로 변경 이력을 조회하여, 매칭된 항목에 대해 AI 서비스로 메시지 템플릿 생성을 요청하고 Knock을 통해 발송 |
| 대화 이력 | 질의/응답 이력 저장·조회·삭제                                                              |

---

## 기술 스택

| 영역 | 기술 |
|---|---|
| Framework | Java 21, Spring Boot 3.5.x |
| DB | PostgreSQL + pgvector (단일 인스턴스, 스키마로 용도 분리) |
| ORM | Spring Data JPA |
| 캐시 | Redis (Refresh Token 저장 및 강제 만료) |
| HTTP 클라이언트 | WebClient (서울시 Open API, AI 서비스 연동) |
| 인증 | OAuth 2.0 소셜 로그인 + JWT (spring-security-oauth2-client, resource-server) |
| 빌드 | Gradle |

---

## 프로젝트 구조

헥사고날 아키텍처(Ports & Adapters)를 적용한 **BC(Bounded Context) 수직 Gradle 멀티모듈** 프로젝트입니다.
각 BC 모듈이 `domain/`, `application/`, `port/`, `adapter/` 내부 구조를 독립적으로 보유합니다.

```
on-seoul-api/
├── common/            # 전역 예외(ErrorCode, OnSeoulApiException). 모든 모듈 의존 가능
├── user/              # 인증 BC — OAuth2 로그인, JWT, 사용자 프로필
├── chat/              # 채팅 BC — ChatRoom, ChatMessage, AI 서비스 SSE 릴레이
├── collection/        # 수집 BC — 서울 Open API 수집, ServiceChangeLog, 임베딩 연동
├── notification/      # 알림 BC — 구독, Knock 발송(SMS/이메일), 템플릿 생성
├── bootstrap/         # Web API 부트스트랩 (OnSeoulApiApplication, SecurityConfig 조합)
├── docs/              # ADR, 구현 목록
├── schema/            # DB 마이그레이션 스크립트
├── build.gradle
└── settings.gradle
```

각 BC 모듈 내부 구조:

```
<bc>/
├── domain/            # 순수 도메인 (프레임워크 무의존)
├── application/       # 유스케이스 구현체 (spring-tx만 허용)
├── port/
│   ├── in/            # 인바운드 포트 — UseCase 인터페이스
│   └── out/           # 아웃바운드 포트 — LoadXxxPort, SaveXxxPort 등
└── adapter/
    ├── in/            # REST 컨트롤러, Security 필터 등
    └── out/           # JPA, Redis, WebClient 어댑터
```

### 모듈 의존 관계

```
bootstrap
  ├── user        ──▶ common
  ├── chat        ──▶ common, collection
  ├── collection  ──▶ common
  └── notification──▶ common
```

- **common**: 전역 예외·유틸. 프레임워크 의존 없음.
- **user**: OAuth2·JWT 인증. 다른 BC에 의존하지 않음 (알림 구독은 opt-in 모델).
- **chat**: 채팅 세션·메시지. AI 서비스 SSE 릴레이.
- **collection**: 서울 Open API 수집·변경 감지. 다른 BC에 의존하지 않음.
- **notification**: 구독·발송 스케줄링. user/collection을 ID로만 참조 (객체 참조 금지).
- **bootstrap**: 모든 BC를 조합해 Spring Boot 애플리케이션으로 실행.

---

## 수집 대상 API

서울 열린데이터 광장에서 5개 카테고리의 공공서비스 예약 데이터를 수집합니다.

| API명 | 데이터셋 ID |
|---|---|
| 문화행사 공공서비스 예약 | OA-2269 |
| 체육시설 공공서비스 예약 | OA-2266 |
| 시설대관 공공서비스 예약 | OA-2267 |
| 교육 공공서비스 예약 | OA-2268 |
| 진료 공공서비스 예약 | OA-2270 |

수집 흐름:

```
수집이력 생성 → Open API 호출 (페이지네이션) → 공통 RDB 스키마 변환
→ 기존 데이터 비교 → 신규/변경/유지 분류 (service_id 기준) → DB Upsert → 수집이력 결과 기록
```

---

## 개발 환경 설정

### 사전 준비

- Java 21+
- PostgreSQL (pgvector 확장 설치)
- Redis

### 실행

```bash
cd on-seoul-api

# 의존성 설치 및 빌드
./gradlew build

# 개발 서버 실행
./gradlew :bootstrap:bootRun

# 테스트
./gradlew test
```

### 헬스체크

```bash
curl http://localhost:8080/actuator/health
```

---

## API 엔드포인트

**인증 / 사용자**

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| GET | `/oauth2/authorization/{provider}` | 소셜 로그인 시작 (Google 등) | X |
| GET | `/login/oauth2/code/{provider}` | OAuth2 콜백 — JWT 발급 | X |
| POST | `/auth/token/refresh` | Access Token 재발급 (Refresh Token 사용) | X |
| POST | `/auth/logout` | 로그아웃 (Refresh Token 무효화) | O |
| GET | `/auth/me` | 내 프로필 조회 | O |
| PATCH | `/api/users/me/contact` | 연락처(전화번호) 등록/수정 | O |

**챗봇**

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| POST | `/api/chat/query` | 챗봇 질의 (AI 서비스 위임, SSE) — 직전 5턴 history를 body로 전달해 멀티턴 맥락 유지 | O |
| GET | `/api/chat/rooms` | 대화방 목록 조회 (cursor 페이지네이션) | O |
| GET | `/api/chat/rooms/{roomId}/messages` | 대화방 메시지 이력 조회 | O |
| DELETE | `/api/chat/rooms/{roomId}` | 대화방 삭제 (soft delete) | O |

**알림 구독 / 발송 이력**

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| GET    | `/api/notifications/subscriptions` | 내 구독 목록 | O |
| POST   | `/api/notifications/subscriptions` | 구독 생성 | O |
| PATCH  | `/api/notifications/subscriptions/{id}` | 구독 수정 (filter/channels) | O |
| DELETE | `/api/notifications/subscriptions/{id}` | 구독 해지 | O |
| GET    | `/api/notifications/dispatches` | 발송 이력 (cursor 페이지네이션) | O |

**관리자**

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| POST | `/admin/collection/trigger` | 수집 수동 트리거 | 관리자 |

---

## 관련 문서

- [프로젝트 전체 구조](../docs/architecture.md)
- [AI 서비스 구현 목록](../on-seoul-agent/docs/ai-service-implementation.md)
- [알림 BC 상세 문서](./notification/README.md)
