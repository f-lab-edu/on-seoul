# 헥사고날 아키텍처 전환

> **대상:** `on-seoul-api` (Spring Boot 멀티모듈 프로젝트)
> **작성일:** 2026-04-25
> **상태:** 제안 (Epic 5 다음 Phase 진행 전 전환)

---

## 1. 배경

현재 구조는 전통적인 레이어드 아키텍처를 따르며, 다음과 같은 결합이 누적되고 있다.

| 위치 | 문제 |
|---|---|
| `domain/` 모듈 | JPA 엔티티(`@Entity`)와 Spring Data Repository가 도메인 모듈에 직접 위치 → 도메인이 영속성에 결합 |
| `app/auth/AuthService` | `UserRepository`, `StringRedisTemplate`, `JwtProvider`를 직접 의존 → 인프라 교체/모킹 비용 증가 |
| `collector/service` | 서울 Open API 클라이언트(`SeoulOpenApiClient`), 카카오 클라이언트, JPA 리포지토리가 한 서비스에 혼재 |
| `app/security/OAuth2LoginSuccessHandler` | 비즈니스 로직(유저 upsert, 토큰 발급, Redis 저장)과 HTTP 응답 직렬화가 한 클래스에 |

앞으로 추가될 Phase(채팅 SSE 릴레이, FCM 알림, 변경 감지 파이프라인)는 외부 시스템 의존성이 더 늘어난다. **지금 시점에 도메인-인프라 경계를 명확히 그어두지 않으면** 테스트 더블이 늘어나고, 인수 테스트 작성 비용이 가파르게 상승한다.

---

## 2. 목표

1. **도메인을 프레임워크로부터 격리한다.** 도메인 모듈은 Spring/JPA/Redis 등에 컴파일 의존하지 않는다.
2. **유스케이스를 일급 단위로 만든다.** 진입점(REST/스케줄러/OAuth2 핸들러)과 무관하게 동일한 유스케이스를 호출한다.
3. **외부 시스템은 어댑터로 교체 가능해야 한다.** Seoul Open API, Kakao Geocoding, Redis, FCM, Postgres는 모두 Outbound Port 뒤에 둔다.
4. **테스트는 도메인/유스케이스 단위에서 in-memory fake로 빠르게 돌아간다.** Spring 컨텍스트는 어댑터 통합 테스트에서만 띄운다.

---

## 3. 타겟 구조

### 3.1 모듈 레이아웃

```
on-seoul-api/
├── common/              # 공용 유틸, 예외 베이스 (현행 유지)
├── domain/              # 순수 도메인 (프레임워크 무의존)
│   ├── model/           # 엔티티, VO, 열거형  (User, ChatRoom, UserStatus 등)
│   ├── event/           # 도메인 이벤트
│   └── port/
│       ├── in/          # 유스케이스 인터페이스 (XxxUseCase)
│       └── out/         # 외부 의존성 인터페이스 (XxxPort)
├── application/         # 유스케이스 구현 (도메인 + 포트 조립, Spring 무의존이 이상적)
│   └── service/         # XxxService implements XxxUseCase
├── adapter/             # 어댑터 (Spring 의존 OK)
│   ├── in/
│   │   ├── web/         # REST 컨트롤러, DTO, exception handler
│   │   ├── security/    # OAuth2LoginSuccessHandler, JwtAuthenticationFilter (얇게)
│   │   └── scheduler/   # @Scheduled 진입점
│   └── out/
│       ├── persistence/ # JPA 엔티티 + 매퍼 + Repository 어댑터
│       ├── redis/       # Refresh Token 
│       ├── seoulapi/    # 서울 Open API 클라이언트 어댑터
│       ├── kakao/       # Kakao Geocoding 어댑터
│       └── fcm/         # FCM 발송 어댑터
└── app/                 # Spring Boot 부트스트랩 (Application.java, application.yml만)
```

> **주의:** `application` 모듈을 Spring 무의존으로 두는 것은 이상이지만, 트랜잭션 경계(`@Transactional`)는 현실적으로 application 레이어에 둔다. → `spring-tx`만 의존하는 절충을 권장.

### 3.2 의존 방향 (단방향)

```
adapter ──▶ application ──▶ domain
   │                          ▲
   └──────────────────────────┘  (out 어댑터는 domain.port.out 구현)
```

- `domain` → 의존 없음
- `application` → `domain`만
- `adapter.in` → `application` (유스케이스 호출), `domain` (입력 매핑)
- `adapter.out` → `domain` (포트 구현), `application` 의존 금지
- `app` → 모든 모듈 (조립만)

### 3.3 Gradle 모듈 의존성

```groovy
// settings.gradle
include 'common', 'domain', 'application', 'adapter', 'app'

// adapter/build.gradle
dependencies {
    implementation project(':domain')
    implementation 'org.springframework.boot:spring-boot-starter-data-jpa'
    implementation 'org.springframework.boot:spring-boot-starter-data-redis'
    implementation 'org.springframework.boot:spring-boot-starter-web'
    implementation 'org.springframework.boot:spring-boot-starter-security'
    implementation 'org.springframework.boot:spring-boot-starter-oauth2-client'
}

// application/build.gradle
dependencies {
    implementation project(':domain')
    implementation 'org.springframework:spring-tx'   // @Transactional만
}

// domain/build.gradle
dependencies {
    // 외부 의존성 없음. lombok, jakarta.validation 정도만 허용
}
```

> 어댑터 단일 모듈이 부담스러우면 `adapter-persistence`, `adapter-web` 등으로 추가 분할 가능. 초기에는 단일 모듈로 시작 권장.

---

## 4. 포트 / 어댑터 명명 규칙

| 종류 | 위치 | 명명 | 예시 |
|---|---|---|---|
| Inbound Port (유스케이스) | `domain/port/in` | `XxxUseCase` | `LoginUseCase`, `RefreshTokenUseCase`, `CollectDatasetUseCase` |
| Inbound Port 명령 객체 | `domain/port/in` | `XxxCommand` (record) | `LoginCommand`, `RefreshCommand` |
| Outbound Port | `domain/port/out` | `XxxPort` 또는 `LoadXxxPort`/`SaveXxxPort` | `LoadUserPort`, `SaveUserPort`, `RefreshTokenStorePort`, `SeoulDatasetFetchPort` |
| Inbound Adapter | `adapter/in/...` | `XxxController`, `XxxScheduler` | `AuthController`, `CollectionScheduler` |
| Outbound Adapter | `adapter/out/...` | `XxxJpaAdapter`, `XxxRedisAdapter`, `XxxClient` | `UserJpaAdapter`, `RefreshTokenRedisAdapter`, `SeoulOpenApiClient` |
| Application 서비스 | `application/service` | `XxxService implements XxxUseCase` | `LoginService`, `CollectDatasetService` |

### 포트 분리 원칙

- **CQS 관점에서 Load/Save 분리:** `LoadUserPort`(read), `SaveUserPort`(write)로 나누면 read-only 어댑터가 만들기 쉽고, 의존성 그래프가 명확해진다.
- **포트당 하나의 책임:** 유스케이스가 필요로 하는 메서드만 포함. "전체 DAO" 같은 비대 포트 금지.

---

## 5. 현행 → 타겟 매핑

### 5.1 인증 도메인 (Epic 5에서 작업한 영역)

| 현재 위치 | 타겟 위치 | 비고 |
|---|---|---|
| `domain/.../domain/User.java` | `domain/model/User.java` | `@Entity`/`@Table` 제거 → 순수 POJO |
| `domain/.../repository/UserRepository.java` | (a) `domain/port/out/LoadUserPort`, `SaveUserPort` <br> (b) `adapter/out/persistence/UserJpaRepository` (Spring Data) <br> (c) `adapter/out/persistence/UserPersistenceAdapter` (포트 구현) | JPA 엔티티는 어댑터 내부 별도 클래스(`UserJpaEntity`)로 두고 도메인 모델과 매퍼로 변환 |
| `app/auth/AuthService` | `application/service/RefreshTokenService implements RefreshTokenUseCase` | `JwtProvider`/Redis는 Outbound Port로 추출 |
| `app/security/jwt/JwtProvider` | `domain/port/out/TokenIssuerPort` (인터페이스) <br> `adapter/out/jwt/JjwtTokenIssuer` (구현) | 도메인은 "토큰 발급"이라는 개념만 알고 jjwt 라이브러리는 어댑터에 격리 |
| `app/security/OAuth2LoginSuccessHandler` | `adapter/in/security/OAuth2LoginSuccessHandler` (얇게) <br> + `application/service/SocialLoginService implements SocialLoginUseCase` | 핸들러는 OAuth2User 파싱 + UseCase 호출 + JSON 응답만. upsert/JWT/Redis 로직은 UseCase로 이동 |
| Redis Refresh Token 저장 | `domain/port/out/RefreshTokenStorePort` <br> `adapter/out/redis/RedisRefreshTokenStore` | TTL 상수도 도메인 정책으로 이동 |

### 5.2 수집 도메인 (collector 모듈)

| 현재 | 타겟 |
|---|---|
| `collector/service/*` (수집 오케스트레이션) | `application/service/CollectDatasetService implements CollectDatasetUseCase` |
| `SeoulOpenApiClient` | `domain/port/out/SeoulDatasetFetchPort` + `adapter/out/seoulapi/SeoulOpenApiClient` |
| `KakaoGeocodingClient` | `domain/port/out/GeocodingPort` + `adapter/out/kakao/KakaoGeocodingClient` |
| `collector/repository/*` (JPA) | `domain/port/out/{Load,Save}PublicServicePort` + `adapter/out/persistence/PublicServicePersistenceAdapter` |
| `collector/scheduler` | `adapter/in/scheduler/CollectionScheduler` (UseCase 호출만) |

### 5.3 채팅 / 알림 (앞으로 진행할 Phase)

선제적으로 다음 포트를 정의해두면 후속 작업이 단순해진다.

- `domain/port/out/AiAgentStreamPort` — AI Service SSE 스트림 (어댑터: `WebClientAiAgentClient`)
- `domain/port/out/NotificationDispatchPort` — FCM 발송 (어댑터: `FirebaseMessagingClient`)
- `domain/port/out/AiAgentNotificationTemplatePort` — AI Service `/notification/template` 호출

---

## 6. 의존성 규칙 강제

핵심 경계 두 가지만 **ArchUnit**으로 자동 검증한다. adapter가 단일 모듈이므로 in/out 분리 룰은 불필요하다.

```java
@AnalyzeClasses(packages = "dev.jazzybyte.onseoul")
class HexagonalArchTest {

    // 규칙 1: domain은 Spring/JPA/Jackson에 의존하지 않는다
    @ArchTest
    static final ArchRule domain_must_not_depend_on_frameworks = noClasses()
            .that().resideInAPackage("..domain..")
            .should().dependOnClassesThat().resideInAnyPackage(
                    "org.springframework..",
                    "jakarta.persistence..",
                    "com.fasterxml..");

    // 규칙 2: application은 adapter를 알지 못한다
    @ArchTest
    static final ArchRule application_must_not_depend_on_adapter = noClasses()
            .that().resideInAPackage("..application..")
            .should().dependOnClassesThat().resideInAPackage("..adapter..");
}
```

CI에서 실행되어야 의미가 있다. 도입 시점에 위반 0개 확인 후 머지.

---

## 7. 전환 전략 (단계적, Strangler-Fig)

빅뱅 전환은 위험하다. 다음 순서로 점진 전환한다.

### Phase H-1. 모듈 골격 생성 (브랜치: `refactor/hex-skeleton`)

- [ ] `application`, `adapter` Gradle 모듈 추가
- [ ] `settings.gradle`/`build.gradle` 의존성 정의 (위 3.3 참조)
- [ ] `app` 모듈에서 `Application.java`만 남기고 나머지는 단계적으로 이동
- [ ] ArchUnit 룰 1개(가장 강한 것: domain → spring 금지)부터 적용
- [ ] 검증: 빌드 성공 + 기존 테스트 통과

### Phase H-2. 인증 도메인 이관 (브랜치: `refactor/hex-auth`)

> Epic 5의 결과물을 가장 먼저 옮긴다. 이미 책임 분리가 되어 있어 이관 비용이 낮다.

- [ ] `User`/`UserStatus` → `domain/model/`로 이동, `@Entity` 제거
- [ ] `UserJpaEntity` 신설 (`adapter/out/persistence/`), 매퍼(`UserPersistenceMapper`) 작성
- [ ] `LoadUserPort`, `SaveUserPort` 정의
- [ ] `UserPersistenceAdapter implements LoadUserPort, SaveUserPort`
- [ ] `TokenIssuerPort` + `JjwtTokenIssuer` 어댑터
- [ ] `RefreshTokenStorePort` + `RedisRefreshTokenStore` 어댑터
- [ ] `SocialLoginUseCase`, `RefreshTokenUseCase` 정의 + 구현
- [ ] `OAuth2LoginSuccessHandler` 슬림화 → UseCase 호출만
- [ ] `AuthController` → UseCase 호출만
- [ ] 기존 테스트 모두 그린, 인증 E2E 재확인

### Phase H-3. 수집 도메인 이관 (브랜치: `refactor/hex-collector`)

- [ ] `collector` 모듈을 `application` + `adapter.out.seoulapi` + `adapter.out.kakao` + `adapter.out.persistence` + `adapter.in.scheduler`로 분해
- [ ] 포트 정의 후 어댑터 이동, 서비스 로직은 application으로
- [ ] 기존 수집 테스트 모두 그린

### Phase H-4. 도메인 정리 + ArchUnit 강화

- [ ] 누락된 ArchUnit 룰 추가 (위 6번 전체)
- [ ] `domain` 모듈에 남아있는 Spring/JPA 의존성 제거 검증
- [ ] CI에 ArchUnit 테스트 포함

### Phase H-5. 채팅 / 알림 Phase 진행 (헥사고날 위에서 신규 개발)

- 새 기능은 처음부터 헥사고날 구조로 작성한다.

---

## 8. 트랜잭션 / 영속성 컨텍스트 처리

JPA 엔티티가 어댑터 내부에 있고 도메인은 POJO이므로, **유스케이스 경계에서 매핑이 필요하다.**

```java
// adapter/out/persistence
@Component
@RequiredArgsConstructor
class UserPersistenceAdapter implements LoadUserPort, SaveUserPort {
    private final UserJpaRepository jpaRepository;
    private final UserPersistenceMapper mapper;

    @Override
    public Optional<User> findByProviderAndProviderId(String provider, String providerId) {
        return jpaRepository.findByProviderAndProviderId(provider, providerId)
                .map(mapper::toDomain);
    }

    @Override
    public User save(User user) {
        UserJpaEntity entity = mapper.toEntity(user);
        return mapper.toDomain(jpaRepository.save(entity));
    }
}
```

### 주의 사항

- **dirty checking 미작동:** 도메인 객체를 수정해도 자동 반영되지 않으므로 `save()`를 명시 호출한다.
- **N+1 / 지연 로딩:** 트랜잭션이 어댑터에서 끝나므로 영속성 컨텍스트 밖에서 LAZY 접근하면 예외 발생. 어댑터에서 필요한 데이터를 모두 로딩 후 도메인 객체로 변환해야 한다.
- **Optimistic Locking / 버전 필드:** JPA 엔티티에만 두고, 도메인에는 노출하지 않는다 (또는 도메인 VO로 추상화).

---

## 9. 테스트 전략

| 레이어 | 테스트 종류 | 도구 |
|---|---|---|
| `domain` | 단위 테스트 | JUnit5 only, Spring 없음 |
| `application` | 유스케이스 단위 테스트 | JUnit5 + Mockito (포트 모킹) 또는 in-memory fake 어댑터 |
| `adapter.out.persistence` | 슬라이스 테스트 | `@DataJpaTest` + Testcontainers(Postgres+pgvector) |
| `adapter.out.redis` | 슬라이스 테스트 | Testcontainers(Redis) |
| `adapter.in.web` | 슬라이스 테스트 | `@WebMvcTest` + UseCase 모킹 |
| 전체 | E2E | `@SpringBootTest` (소수만) |

> 현재 `OAuth2LoginSuccessHandlerIntegrationTest`처럼 Spring 없이 핸들러를 직접 검증하는 패턴은 **유지하면서**, UseCase 단위로 한 단계 더 내릴 수 있다.

---

## 10. 의사결정 / 트레이드오프

| 결정 | 대안 | 채택 근거 |
|---|---|---|
| 어댑터 단일 모듈 | adapter-web/adapter-persistence 분할 | 초기 비용 최소화. 모듈이 비대해지면 분할 |
| 도메인 모듈 = 순수 POJO | JPA 엔티티를 도메인에 두기 | 영속성 결합 제거. 매퍼 비용 < 격리 가치 |
| application은 spring-tx 의존 허용 | 완전 무의존 | 트랜잭션을 어댑터로 내리면 유스케이스 경계가 흐려짐 |
| ArchUnit으로 강제 | 코드리뷰만 | 사람 검토는 누락됨. 자동화 필수 |
| Strangler-Fig 전환 | 빅뱅 리팩터 | 인증 E2E가 이미 동작 중. 회귀 위험 회피 |

---

## 11. 참고

- Alistair Cockburn, "Hexagonal Architecture" (2005)
- Tom Hombergs, *Get Your Hands Dirty on Clean Architecture* — Spring Boot 환경의 헥사고날 구현 레퍼런스
- ArchUnit User Guide — https://www.archunit.org/userguide/html/000_Index.html

---

## 12. 변경 이력

| 날짜 | 변경 | 작성자 |
|---|---|---|
| 2026-04-25 | 초안 작성 (Epic 5 직후, Phase 14 진행 전) | changha |
