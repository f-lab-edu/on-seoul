# on-seoul-api

on-seoul 프로젝트의 API 서비스입니다. 인증, 데이터 수집 스케줄링, 알림 발송, 대화 이력 관리를 담당합니다.

---

## 주요 역할

| 역할 | 설명 |
|---|---|
| 데이터 수집 | 서울 열린데이터 광장 5개 API에서 공공서비스 예약 데이터를 주기적으로 수집하고 변경 감지 |
| 인증 | OAuth 2.0 소셜 로그인 + JWT (Access Token / Refresh Token) |
| 챗봇 릴레이 | 사용자 질의를 AI 서비스(`on-seoul-agent`)에 위임하고 SSE 응답을 프론트엔드에 릴레이 |
| 알림 발송 | 서비스 상태 변경 감지 시 AI 서비스로 메시지 템플릿 생성을 요청하고 FCM으로 발송 |
| 대화 이력 | 질의/응답 이력 저장 및 조회 |

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

헥사고날 아키텍처(Ports & Adapters)를 적용한 Gradle 멀티모듈 프로젝트입니다.

```
on-seoul-api/                        # 루트 (공통 빌드 설정)
├── common/                          # 공통 유틸 — 전역 예외(ErrorCode, OnSeoulApiException)
├── domain/                          # 순수 도메인 (프레임워크 무의존)
│   ├── model/                       # 도메인 POJO (User, ChatRoom, PublicServiceReservation 등)
│   └── port/
│       ├── in/                      # 유스케이스 인터페이스 (XxxUseCase, XxxCommand)
│       └── out/                     # 외부 의존성 인터페이스 (LoadXxxPort, SaveXxxPort 등)
├── application/                     # 유스케이스 구현체 (spring-tx만 의존)
│   └── service/                     # SocialLoginService, RefreshTokenService, CollectDatasetService 등
├── adapter/                         # 모든 어댑터 (Spring/JPA/Redis/WebClient 등 의존)
│   ├── in/
│   │   ├── web/                     # REST 컨트롤러, DTO, GlobalExceptionHandler
│   │   └── security/                # SecurityConfig, OAuth2LoginSuccessHandler, JwtAuthenticationFilter
│   └── out/
│       ├── persistence/             # JPA 엔티티 + Repository + PersistenceAdapter
│       ├── redis/                   # RefreshTokenRedisAdapter
│       ├── seoulapi/                # SeoulOpenApiAdapter
│       ├── kakao/                   # KakaoGeocodingAdapter
│       └── aiservice/               # AiServiceAdapter (AI 서비스 WebClient)
├── bootstrap/                       # Web API 부트스트랩 (OnSeoulApiApplication.java + application.yml)
├── collector/                       # 수집 배치 부트스트랩 (CollectorApplication.java + CollectionScheduler)
├── docs/                            # 구현 문서
├── build.gradle                     # 루트 빌드 설정 (subprojects 공통)
└── settings.gradle                  # 모듈 선언
```

### 모듈 의존 관계

```
adapter ──▶ application ──▶ domain
   │                          ▲
   └──────────────────────────┘  (adapter.out이 domain.port.out 구현)

bootstrap  → adapter, application, domain, common  (Web API 부트)
collector  → adapter, application, domain, common  (수집 배치 부트)
```

- **common**: 프레임워크 의존 없는 공통 유틸(전역 예외). 모든 모듈이 의존 가능.
- **domain**: Spring/JPA/Redis에 컴파일 의존하지 않는 순수 POJO + 포트 인터페이스.
- **application**: 유스케이스 구현체. `spring-tx`(@Transactional)만 허용. 어댑터에 의존 금지.
- **adapter**: 모든 인프라 어댑터. domain 포트를 구현하고 application 유스케이스를 호출함.
- **bootstrap**: Web API 전용 부트스트랩. 인증·채팅·수집 수동 트리거 엔드포인트를 제공한다.
- **collector**: 수집 배치 전용 부트스트랩. `@Scheduled` 매일 08시 수집을 담당한다. Web/Security 의존 없음.

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

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| GET | `/oauth2/authorization/{provider}` | 소셜 로그인 시작 (Google 등) | X |
| GET | `/login/oauth2/code/{provider}` | OAuth2 콜백 — JWT 발급 | X |
| POST | `/auth/token/refresh` | Access Token 재발급 (Refresh Token 사용) | X |
| POST | `/auth/logout` | 로그아웃 (Refresh Token 무효화) | O |
| POST | `/query` | 챗봇 질의 (AI 서비스 위임, SSE) | O |
| POST | `/admin/collection/trigger` | 수집 수동 트리거 | 관리자 |

---

## 관련 문서

- [프로젝트 전체 구조](../docs/architecture.md)
- [API 서비스 구현 목록](./docs/api-service-implementation.md)
- [AI 서비스 구현 목록](../on-seoul-agent/docs/ai-service-implementation.md)
