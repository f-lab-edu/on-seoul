# on-seoul-api

on-seoul 프로젝트의 API 서비스입니다. 인증, 세션 관리, 데이터 수집 스케줄링, 알림 발송, 대화 이력 관리를 담당합니다.

---

## 주요 역할

| 역할 | 설명 |
|---|---|
| 데이터 수집 | 서울 열린데이터 광장 5개 API에서 공공서비스 예약 데이터를 주기적으로 수집하고 변경 감지 |
| 인증/세션 | Spring Security 기반 세션 인증 (Redis 세션 스토어) |
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
| 캐시/세션 | Redis (spring-session-data-redis) |
| HTTP 클라이언트 | WebClient (서울시 Open API, AI 서비스 연동) |
| 인증 | Spring Security (세션 기반) |
| 빌드 | Gradle |

---

## 프로젝트 구조

Gradle 멀티모듈로 구성되어 있으며, 루트는 공통 빌드 설정만 담고 각 서브모듈이 역할을 분담합니다.

```
on-seoul-api/                        # 루트 (공통 빌드 설정, 소스 없음)
├── common/                          # 공유 모듈 — JPA 엔티티, DTO, 예외 정의
├── collector/                       # 수집 모듈 — 서울시 Open API 수집 파이프라인
├── app/                             # 앱 모듈 — Spring Boot 진입점, 컨트롤러, 시큐리티
│   └── src/main/java/.../onseoul/
│       ├── OnSeoulApiApplication.java
│       ├── controller/
│       ├── service/
│       ├── scheduler/
│       └── security/
├── migration-scripts/               # DB 마이그레이션 SQL
├── docs/                            # 구현 문서
├── build.gradle                     # 루트 빌드 설정 (subprojects 공통)
└── settings.gradle                  # 모듈 선언 (common, collector, app)
```

### 모듈 의존 관계

```
app → common, collector
collector → common
```

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
./gradlew :app:bootRun

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
| POST | `/auth/signup` | 회원가입 | X |
| POST | `/auth/login` | 로그인 (세션 발급) | X |
| POST | `/auth/logout` | 로그아웃 (세션 만료) | O |
| POST | `/query` | 챗봇 질의 (AI 서비스 위임, SSE) | O |
| POST | `/admin/collection/trigger` | 수집 수동 트리거 | 관리자 |

---

## 관련 문서

- [프로젝트 전체 구조](../docs/architecture.md)
- [API 서비스 구현 목록](./docs/api-service-implementation.md)
- [AI 서비스 구현 목록](../on-seoul-agent/docs/ai-service-implementation.md)
