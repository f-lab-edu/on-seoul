# API 서비스 Rate Limiting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Spring Boot API 서비스에서 JWT 인증된 사용자 기준으로 `/chat/stream` 엔드포인트에 Rate Limiting을 적용한다.

**Architecture:** Redis 기반 Sliding Window Counter 알고리즘으로 사용자당 분당 호출 횟수를 제한한다. Spring Security 필터 체인 이후, SSE 릴레이 전에 적용하여 AI 서비스 호출 자체를 차단한다. Redis 장애 시 fail-open으로 동작한다.

**Tech Stack:** Java 21, Spring Boot 3.5.x, Spring Security, Redis (Lettuce), bucket4j-spring-boot-starter (Redis 백엔드)

---

## 책임 경계

AI 서비스(FastAPI)는 RPM 제어를 하지 않는다.
API 서비스가 JWT 인증 컨텍스트를 보유하고 있어 사용자 기준 제어가 가능하고,
AI 서비스는 API 서비스의 신뢰된 내부 호출 대상이므로 이 레이어에서 Rate Limiting이 적합하다.

---

## 알고리즘 선택: Sliding Window Counter

| 알고리즘 | 이유 |
|---------|------|
| Fixed Window | 윈도우 경계 버스트 허용 (59초 + 61초 = 2배) — 탈락 |
| Token Bucket | 버스트 허용이 목적 — LLM 비용 보호에 역행 — 탈락 |
| Leaky Bucket | 일정 처리율 보장 전제 — API 서비스는 적합하나 구현 복잡 |
| **Sliding Window Counter** | 정확도·메모리 균형. bucket4j가 내장 지원 — 채택 |

---

## File Map

| 파일 | 역할 | 변경 |
|------|------|------|
| `pom.xml` / `build.gradle` | bucket4j-spring-boot-starter 의존성 추가 | 수정 |
| `application.yml` | Rate Limit 설정값 추가 | 수정 |
| `config/RateLimitConfig.java` | bucket4j Bandwidth 빈 설정 | 신규 생성 |
| `filter/RateLimitFilter.java` | OncePerRequestFilter — JWT userId 추출 후 bucket 확인 | 신규 생성 |
| `security/SecurityConfig.java` | RateLimitFilter를 필터 체인에 등록 | 수정 |
| `RateLimitFilterTest.java` | 필터 단위 테스트 | 신규 생성 |

---

## Task 1: 의존성 및 설정값 추가

**Files:**
- Modify: `pom.xml` 또는 `build.gradle`
- Modify: `src/main/resources/application.yml`

- [ ] **Step 1: `pom.xml`에 bucket4j 의존성 추가**

```xml
<!-- pom.xml -->
<dependency>
    <groupId>com.giffing.bucket4j.spring.boot.starter</groupId>
    <artifactId>bucket4j-spring-boot-starter</artifactId>
    <version>0.12.7</version>
</dependency>
```

Gradle 사용 시:
```groovy
implementation 'com.giffing.bucket4j.spring.boot.starter:bucket4j-spring-boot-starter:0.12.7'
```

- [ ] **Step 2: `application.yml`에 Rate Limit 설정값 추가**

```yaml
app:
  rate-limit:
    enabled: true
    requests-per-minute: 20   # 사용자당 분당 최대 요청 수
    window-seconds: 60        # 슬라이딩 윈도우 크기 (초)
```

- [ ] **Step 3: 빌드 확인**

```bash
./mvnw compile   # 또는 ./gradlew compileJava
```
Expected: BUILD SUCCESS

- [ ] **Step 4: 커밋**

```bash
git add pom.xml src/main/resources/application.yml
git commit -m "feat: Rate Limiting 의존성 및 설정값 추가"
```

---

## Task 2: RateLimitFilter 구현

**Files:**
- Create: `src/main/java/.../filter/RateLimitFilter.java`
- Create: `src/main/java/.../config/RateLimitConfig.java`

### 동작 설계

```
Redis key: rate_limit:user:{userId}
ZADD key {now} {now}
ZREMRANGEBYSCORE key 0 {now - window_ms}   (윈도우 밖 항목 제거)
ZCARD key > requests-per-minute → 429
EXPIRE key {window-seconds}
```

bucket4j를 사용하면 위 Redis 연산을 라이브러리가 처리한다.

- [ ] **Step 1: 실패하는 테스트 작성**

```java
// src/test/java/.../filter/RateLimitFilterTest.java
@ExtendWith(MockitoExtension.class)
class RateLimitFilterTest {

    @Mock
    private BucketProxyManager<String> bucketManager;

    @Mock
    private RateLimitProperties properties;

    @InjectMocks
    private RateLimitFilter filter;

    @Test
    void 인증된_사용자_정상_요청_통과() throws Exception {
        // given
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/chat/stream");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        Bucket bucket = mock(Bucket.class);
        ConsumptionProbe probe = mock(ConsumptionProbe.class);
        given(probe.isConsumed()).willReturn(true);
        given(bucket.tryConsumeAndReturnRemaining(1)).willReturn(probe);
        given(bucketManager.getProxy(any(), any())).willReturn(bucket);
        given(properties.isEnabled()).willReturn(true);

        Authentication auth = new UsernamePasswordAuthenticationToken(
            "user-123", null, List.of());
        SecurityContextHolder.getContext().setAuthentication(auth);

        // when
        filter.doFilterInternal(request, response, chain);

        // then
        assertThat(response.getStatus()).isNotEqualTo(429);
        verify(chain).doFilter(request, response);
    }

    @Test
    void 한도_초과_시_429_반환() throws Exception {
        // given
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/chat/stream");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        Bucket bucket = mock(Bucket.class);
        ConsumptionProbe probe = mock(ConsumptionProbe.class);
        given(probe.isConsumed()).willReturn(false);
        given(bucket.tryConsumeAndReturnRemaining(1)).willReturn(probe);
        given(bucketManager.getProxy(any(), any())).willReturn(bucket);
        given(properties.isEnabled()).willReturn(true);

        Authentication auth = new UsernamePasswordAuthenticationToken(
            "user-123", null, List.of());
        SecurityContextHolder.getContext().setAuthentication(auth);

        // when
        filter.doFilterInternal(request, response, chain);

        // then
        assertThat(response.getStatus()).isEqualTo(429);
        verify(chain, never()).doFilter(any(), any());
    }

    @Test
    void disabled_설정이면_필터_통과() throws Exception {
        // given
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/chat/stream");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);
        given(properties.isEnabled()).willReturn(false);

        // when
        filter.doFilterInternal(request, response, chain);

        // then
        verify(chain).doFilter(request, response);
        verifyNoInteractions(bucketManager);
    }

    @Test
    void Redis_장애_시_fail_open_통과() throws Exception {
        // given
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/chat/stream");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);
        given(properties.isEnabled()).willReturn(true);
        given(bucketManager.getProxy(any(), any())).willThrow(new RuntimeException("redis down"));

        Authentication auth = new UsernamePasswordAuthenticationToken(
            "user-123", null, List.of());
        SecurityContextHolder.getContext().setAuthentication(auth);

        // when
        filter.doFilterInternal(request, response, chain);

        // then
        assertThat(response.getStatus()).isNotEqualTo(429);
        verify(chain).doFilter(request, response);
    }
}
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
./mvnw test -Dtest=RateLimitFilterTest
```
Expected: FAIL (클래스 없음)

- [ ] **Step 3: `RateLimitProperties.java` 생성**

```java
// src/main/java/.../config/RateLimitProperties.java
@ConfigurationProperties(prefix = "app.rate-limit")
@Getter @Setter
public class RateLimitProperties {
    private boolean enabled = true;
    private int requestsPerMinute = 20;
    private int windowSeconds = 60;
}
```

- [ ] **Step 4: `RateLimitConfig.java` 생성**

```java
// src/main/java/.../config/RateLimitConfig.java
@Configuration
@EnableConfigurationProperties(RateLimitProperties.class)
public class RateLimitConfig {

    @Bean
    public BucketProxyManager<String> bucketProxyManager(
            RedisTemplate<String, byte[]> redisTemplate,
            RateLimitProperties properties) {
        LettuceBasedProxyManager<String> proxyManager = LettuceBasedProxyManager
            .builderFor(/* Lettuce connection */)
            .withExpirationAfterWrite(
                Duration.ofSeconds(properties.getWindowSeconds()))
            .build();

        return proxyManager;
    }

    public BucketConfiguration bucketConfiguration(RateLimitProperties properties) {
        return BucketConfiguration.builder()
            .addLimit(Bandwidth.builder()
                .capacity(properties.getRequestsPerMinute())
                .refillGreedy(properties.getRequestsPerMinute(),
                    Duration.ofSeconds(properties.getWindowSeconds()))
                .build())
            .build();
    }
}
```

- [ ] **Step 5: `RateLimitFilter.java` 생성**

```java
// src/main/java/.../filter/RateLimitFilter.java
@Component
@RequiredArgsConstructor
@Slf4j
public class RateLimitFilter extends OncePerRequestFilter {

    private static final String RATE_LIMIT_KEY_PREFIX = "rate_limit:user:";
    private static final Set<String> RATE_LIMITED_PATHS = Set.of("/chat/stream");

    private final BucketProxyManager<String> bucketManager;
    private final RateLimitProperties properties;
    private final RateLimitConfig rateLimitConfig;

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain) throws ServletException, IOException {

        if (!properties.isEnabled() || !RATE_LIMITED_PATHS.contains(request.getRequestURI())) {
            filterChain.doFilter(request, response);
            return;
        }

        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        if (auth == null || !auth.isAuthenticated()) {
            filterChain.doFilter(request, response);
            return;
        }

        String userId = auth.getName();
        String key = RATE_LIMIT_KEY_PREFIX + userId;

        try {
            Bucket bucket = bucketManager.getProxy(
                key,
                () -> rateLimitConfig.bucketConfiguration(properties));
            ConsumptionProbe probe = bucket.tryConsumeAndReturnRemaining(1);

            if (!probe.isConsumed()) {
                response.setStatus(HttpStatus.TOO_MANY_REQUESTS.value());
                response.setContentType(MediaType.APPLICATION_JSON_VALUE);
                response.getWriter().write(
                    "{\"message\":\"요청 횟수가 초과되었습니다. 잠시 후 다시 시도해 주세요.\"}");
                return;
            }
        } catch (Exception e) {
            log.warn("Rate Limit Redis 오류 — fail-open으로 요청 통과", e);
        }

        filterChain.doFilter(request, response);
    }
}
```

- [ ] **Step 6: `SecurityConfig.java`에 필터 등록**

```java
// SecurityConfig.java — filterChain 메서드 내 적절한 위치에 추가
.addFilterAfter(rateLimitFilter, JwtAuthenticationFilter.class)
```

- [ ] **Step 7: 테스트 통과 확인**

```bash
./mvnw test -Dtest=RateLimitFilterTest
```
Expected: 4 passed

- [ ] **Step 8: 커밋**

```bash
git add src/main/java/.../filter/RateLimitFilter.java \
        src/main/java/.../config/RateLimitConfig.java \
        src/main/java/.../config/RateLimitProperties.java \
        src/main/java/.../security/SecurityConfig.java
git commit -m "feat: 사용자 기준 Rate Limiting 구현 (Sliding Window, fail-open)"
```

---

## 완료 기준 체크리스트

- [ ] 테스트 전체 통과
- [ ] `app.rate-limit.enabled=false` 설정으로 비활성화 가능
- [ ] Redis 장애 시 서비스 정상 동작 (fail-open)
- [ ] `/chat/stream` 외 경로는 Rate Limit 미적용
- [ ] 인증되지 않은 요청은 Rate Limit 미적용 (Spring Security가 별도 처리)
- [ ] 429 응답 body가 JSON 형식

---

## 참고

- **AI 서비스 Concurrent Limit과의 관계**: API 서비스 Rate Limit은 RPM 제어(분당 횟수), AI 서비스 Concurrent Limit은 동시 실행 수 제어. 두 레이어는 독립적으로 동작한다.
- **bucket4j 문서**: https://bucket4j.com
- **Redis key 네임스페이스**: `rate_limit:user:{userId}` — AI 서비스의 `concurrent:room:{room_id}`와 충돌하지 않음