# 테스트 코드 작성 계획

헥사고날 아키텍처 전환 과정에서 삭제·누락된 테스트를 복구하고,  
신규 클래스에 대한 테스트를 추가하기 위한 계획 문서.

---

## 현황 요약

### 기존 커버리지 (유지)

| 모듈 | 테스트 파일 | 상태 |
|---|---|---|
| adapter | `HexagonalArchTest` | ✅ |
| adapter | `CookieOAuth2AuthorizationRequestRepositoryTest` | ✅ |
| adapter | `JwtAuthenticationFilterTest` | ✅ |
| adapter | `JwtTokenIssuerTest` | ✅ |
| adapter | `AiServiceAdapterTest` | ✅ |
| application | `ChatStreamServiceTest` | ✅ |
| application | `RefreshTokenServiceTest` (+ LogoutService 포함) | ✅ |
| application | `SendQueryServiceTest` | ✅ |
| bootstrap | `AuthControllerTest`, `CollectionControllerTest`, `ChatControllerTest` | ✅ |
| bootstrap | `OAuth2LoginSuccessHandlerIntegrationTest`, `SecurityConfigTest` | ✅ |

### 정리 대상 (빈 스텁)

아래 3개는 헥사고날 전환 시 마이그레이션 안내 주석만 남긴 빈 파일이다.  
실제 테스트가 이미 올바른 모듈에 작성됐으므로 **삭제한다.**

| 파일 (bootstrap) | 이유 |
|---|---|
| `auth/AuthServiceTest.java` | `application/RefreshTokenServiceTest`로 이관 완료 |
| `security/jwt/JwtProviderTest.java` | `adapter/JwtTokenIssuerTest`로 이관 완료 |
| `security/jwt/JwtAuthenticationFilterTest.java` | `adapter/JwtAuthenticationFilterTest`로 이관 완료 |

---

## 작성 계획

우선순위는 **비즈니스 로직 밀도** 기준으로 결정한다.  
`adapter`, `application` 모듈이 1순위이며, 각 모듈 안에서도 핵심 도메인 연산이 많은 클래스를 먼저 작성한다.

---

### P1 — application 서비스 (단위 테스트, Mockito)

#### T1. `UpsertServiceTest`
> 수집 파이프라인의 핵심. 신규/변경/유지 분류 정확성과 ChangeLog 생성 여부가 핵심 검증 대상.

- [ ] 빈 incoming 리스트 → `UpsertResult.empty()` 반환, DB 조회 없음
- [ ] 전체 신규(existing 없음) → `save()` N회, `newCount = N`
- [ ] 전체 유지(핵심 필드 동일) → `save()` 0회, `unchangedCount = N`
- [ ] serviceStatus 변경 → `save()` 호출, `updatedCount` 증가, ChangeLog 1개 생성
- [ ] receiptStartDt 변경 → ChangeLog 1개 생성
- [ ] receiptEndDt 변경 → ChangeLog 1개 생성
- [ ] 핵심 필드 3개 모두 변경 → ChangeLog 3개 생성
- [ ] 신규/변경/유지 혼합 → 각 카운트 정확성
- [ ] changeLogs 비어있으면 `saveAll()` 미호출

```
위치: application/src/test/java/.../application/service/UpsertServiceTest.java
의존: LoadPublicServicePort, SavePublicServicePort, SaveServiceChangeLogPort (Mock)
```

---

#### T2. `CollectDatasetServiceTest`
> 수집 오케스트레이션. 부분 실패 처리, deletion sweep 조건이 핵심.

- [ ] 활성 소스 없음 → `fetchPort` 미호출, 조기 반환
- [ ] 단일 소스 성공 → history 2회 저장(create + complete), `geocodingService.fillMissingCoords()` 호출
- [ ] 단일 소스 fetchAll 예외 → history fail 상태로 저장, 예외 전파 없음
- [ ] 소스 5개 중 1개 실패 → 나머지 4개 계속 수집, `allSucceeded = false`이면 deletion sweep 건너뜀
- [ ] 모든 소스 성공 → deletion sweep(`performDeletionSweep`) 실행됨
- [ ] deletion sweep — seen 목록에 없는 레코드 soft-delete 후 `saveAll()` 호출
- [ ] deletion sweep — 삭제 대상 없으면 `saveAll()` 미호출

```
위치: application/src/test/java/.../application/service/CollectDatasetServiceTest.java
의존: LoadApiSourceCatalogPort, SaveCollectionHistoryPort, SeoulDatasetFetchPort, UpsertService, GeocodingService, LoadPublicServicePort, SavePublicServicePort (Mock)
```

---

#### T3. `SocialLoginServiceTest`
> 신규/기존 유저 분기, SUSPENDED 차단, 토큰 발급 흐름.

- [ ] 신규 유저 → `saveUserPort.save(User.create(...))`, 토큰 발급, RT 저장
- [ ] 기존 유저 → `updateProfile()` + `saveUserPort.save(existing)`, 토큰 발급
- [ ] ACTIVE 유저 → 정상 `TokenResponse` 반환
- [ ] SUSPENDED 유저 → `OnSeoulApiException(FORBIDDEN)` 발생
- [ ] DELETED 유저 → `OnSeoulApiException(FORBIDDEN)` 발생
- [ ] `refreshTokenStorePort.save(userId, rt, ttlMinutes)` 호출 검증

```
위치: application/src/test/java/.../application/service/SocialLoginServiceTest.java
의존: LoadUserPort, SaveUserPort, TokenIssuerPort, RefreshTokenStorePort (Mock)
```

---

#### T4. `GeocodingServiceTest`
> 캐시 동작, blank placeName 스킵, 좌표 채움 후 저장.

- [ ] 좌표 누락 레코드 없음 → `geocodingPort` 미호출
- [ ] placeName blank/null → 해당 레코드 스킵, geocoding 호출 없음
- [ ] geocoding 성공 → `updateCoords()` + `save()` 호출
- [ ] geocoding 실패(empty) → 저장 없음
- [ ] 동일 placeName 2건 → geocodingPort 1회 호출(캐시 동작 확인)

```
위치: application/src/test/java/.../application/service/GeocodingServiceTest.java
의존: GeocodingPort, LoadPublicServicePort, SavePublicServicePort (Mock)
```

---

### P2 — adapter.out 단위 테스트

#### T5. `PublicServiceRowMapperTest`
> DTO → 도메인 엔티티 변환. 날짜 파싱 실패, 필수 필드 null, 좌표 null 처리.

- [ ] 정상 필드 전체 → Optional 반환, 각 필드 매핑 정확성
- [ ] `service_id` null → Optional.empty() + ERROR 로그
- [ ] `service_name` null → Optional.empty() + ERROR 로그
- [ ] 날짜 문자열 정상 포맷(`"2025/01/01 00:00:00"`) → `LocalDateTime` 변환
- [ ] 날짜 문자열 이상 포맷 → null 또는 기본값 처리(예외 전파 없음)
- [ ] X/Y 좌표 null → coordX/coordY null로 저장(Geocoding fallback 대상)
- [ ] X/Y 좌표 빈 문자열 → null 처리

```
위치: adapter/src/test/java/.../adapter/out/seoulapi/PublicServiceRowMapperTest.java
의존: 없음 (순수 단위 테스트)
```

---

#### T6. `SeoulOpenApiAdapterTest`
> WebClient 페이지네이션, 재시도, 서버 오류 응답 처리.

- [ ] 200건 미만(단일 페이지) → 1회 호출로 완료
- [ ] 200건(2페이지 경계) → 2회 호출
- [ ] 응답 `list_total_count = 0` → 빈 리스트 반환
- [ ] 서버 5xx 오류 → 3회 재시도 후 예외 발생
- [ ] `RowMapper`가 Optional.empty() 반환한 row → 결과 리스트에서 제외

```
위치: adapter/src/test/java/.../adapter/out/seoulapi/SeoulOpenApiAdapterTest.java
의존: WireMock (MockWebServer) 또는 `@WebClientTest`
```

---

#### T7. `KakaoGeocodingAdapterTest`
> Geocoding API 호출, 결과 파싱, API 키 없을 때 empty 반환.

- [ ] 정상 응답(documents[0].x, y) → `Optional.of(BigDecimal[]{x, y})`
- [ ] documents 빈 배열 → `Optional.empty()`
- [ ] HTTP 4xx/5xx → `Optional.empty()` (예외 전파 없음)
- [ ] API 키 미설정(blank) → geocoding 호출 없이 `Optional.empty()`

```
위치: adapter/src/test/java/.../adapter/out/kakao/KakaoGeocodingAdapterTest.java
의존: WireMock (MockWebServer)
```

---

#### T8. `RefreshTokenRedisAdapterTest`
> Redis 원자 연산(GETDEL), TTL 저장, 삭제.

- [ ] `save()` → TTL(초) 포함해 저장됨
- [ ] `getAndDelete()` — 존재하는 키 → 값 반환 + 키 삭제됨
- [ ] `getAndDelete()` — 존재하지 않는 키 → `Optional.empty()`
- [ ] `getAndDelete()` 동시 2회 호출 → 첫 번째만 값 반환, 두 번째는 empty (원자성)
- [ ] `delete()` → 키 제거됨

```
위치: adapter/src/test/java/.../adapter/out/redis/RefreshTokenRedisAdapterTest.java
의존: @DataRedisTest (Embedded Redis) 또는 Testcontainers Redis
```

---

### P3 — adapter.out 영속성 어댑터 (@DataJpaTest)

> H2 인메모리 DB를 사용하는 슬라이스 테스트. 포트 계약(입출력 형태)이 주요 검증 대상이며,  
> SQL 복잡도가 높은 어댑터 위주로 작성한다.

#### T9. `ChatPersistenceAdapterTest`
- [ ] `saveChatRoom()` → 저장 후 id 반환
- [ ] `findById()` — 존재하는 방 → Optional.of()
- [ ] `findById()` — 없는 id → Optional.empty()
- [ ] `nextSeq()` — 네이티브 시퀀스 채번, 연속 호출 시 단조 증가
- [ ] `saveChatMessage()` → 저장 성공

```
위치: adapter/src/test/java/.../adapter/out/persistence/chat/ChatPersistenceAdapterTest.java
```

---

#### T10. `UserPersistenceAdapterTest`
- [ ] `findByProviderAndProviderId()` — 존재하는 유저 → 반환
- [ ] `findByProviderAndProviderId()` — 미존재 → empty
- [ ] `findById()` — 정상 반환
- [ ] `save()` 신규 → insert (id 채번)
- [ ] `save()` 기존 → update (updatedAt 갱신)

```
위치: adapter/src/test/java/.../adapter/out/persistence/user/UserPersistenceAdapterTest.java
```

---

#### T11. `PublicServiceReservationPersistenceAdapterTest`
- [ ] `findAllByServiceIdIn()` — 일치하는 serviceId만 반환
- [ ] `findAllByCoordXIsNullOrCoordYIsNull()` — 좌표 null 레코드만 반환
- [ ] `findAllByDeletedAtIsNull()` — soft-delete된 레코드 제외
- [ ] `save()` upsert 동작 (serviceId UK 기준)
- [ ] `saveAll()` 목록 저장

```
위치: adapter/src/test/java/.../adapter/out/persistence/reservation/PublicServiceReservationPersistenceAdapterTest.java
```

---

#### T12. `CollectionPersistenceAdapterTest`
- [ ] `save(CollectionHistory)` — 이력 저장, fail/complete 상태 모두
- [ ] `saveAll(ServiceChangeLog list)` — 변경 로그 복수 저장

```
위치: adapter/src/test/java/.../adapter/out/persistence/collection/CollectionPersistenceAdapterTest.java
```

---

### P4 — 정리 작업

#### T13. 빈 스텁 파일 삭제
실제 테스트가 올바른 모듈에 이관 완료됐으므로 가이드 주석만 남은 파일들을 삭제한다.

- [ ] `bootstrap/src/test/.../auth/AuthServiceTest.java` 삭제
- [ ] `bootstrap/src/test/.../security/jwt/JwtProviderTest.java` 삭제
- [ ] `bootstrap/src/test/.../security/jwt/JwtAuthenticationFilterTest.java` 삭제

---

## 작업 순서 권장

```
T1(UpsertService) → T2(CollectDataset) → T3(SocialLogin) → T4(Geocoding)
       ↓
T5(RowMapper) → T6(SeoulOpenApi) → T7(Kakao) → T8(Redis)
       ↓
T9~T12(Persistence) + T13(스텁 삭제)
```

P1을 먼저 작성하면 application 서비스가 포트 모킹에 의존하므로 빠르게 완료된다.  
P2~P3은 외부 의존(WebClient, Redis, JPA)이 필요해 테스트 인프라 설정이 선행돼야 한다.

---

## 테스트 유형별 의존 추가 안내

| 유형 | Gradle 의존 |
|---|---|
| WireMock (SeoulApi, Kakao) | `testImplementation 'org.wiremock:wiremock-standalone:3.x'` |
| Embedded Redis | `testImplementation 'com.github.codemonstur:embedded-redis:1.x'` 또는 Testcontainers |
| @DataJpaTest (H2) | `testImplementation 'com.h2database:h2'` (bootstrap 모듈에 이미 있을 수 있음) |
| Reactor StepVerifier | `testImplementation 'io.projectreactor:reactor-test'` (application 모듈에 추가 필요) |
