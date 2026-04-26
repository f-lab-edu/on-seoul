# `collector` 모듈 정리 계획

## 1. 현재 상태

`settings.gradle`에는 `collector` 모듈이 등록되어 있으나, **헥사고날 전환(H-1 ~ H-4) 과정에서 모든 코드가 다른 모듈로 이식**되었고 `collector/` 자체는 어디에서도 의존하지 않는 고아 모듈이다.

### 의존 그래프

```
app ──▶ adapter ──▶ application ──▶ domain ──▶ common
                                                  ▲
                              collector ──────────┘   ← 어디서도 참조하지 않음
```

- `app/build.gradle` → `collector` 의존 없음 → `app.jar` 산출물에 미포함
- `collector/build.gradle` → `common`, `domain`만 의존 (`adapter`/`application` 미참조)

### 코드 중복 현황 (모두 헥사고날 모듈로 이식 완료)

| `collector/` 원본 | 헥사고날 이식 위치 |
|---|---|
| `SeoulOpenApiClient.java` | `adapter/out/seoulapi/SeoulOpenApiAdapter.java` |
| `KakaoGeocodingClient.java` | `adapter/out/kakao/KakaoGeocodingAdapter.java` |
| `PublicServiceRowMapper.java` | `adapter/out/seoulapi/PublicServiceRowMapper.java` |
| `domain/CollectionHistory.java` | `domain/model/CollectionHistory.java` |
| `domain/ServiceChangeLog.java` | `domain/model/ServiceChangeLog.java` |
| `domain/ApiSourceCatalog.java` | `domain/model/ApiSourceCatalog.java` |
| `dto/PublicServiceRow.java` | `adapter/out/seoulapi/PublicServiceRow.java` |
| `dto/SeoulApiResponse.java` | `adapter/out/seoulapi/SeoulApiResponse.java` |
| `dto/KakaoGeocodingResponse.java` | `adapter/out/kakao/KakaoGeocodingResponse.java` |
| `enums/ChangeType.java` | `domain/model/ChangeType.java` |
| `enums/CollectionStatus.java` | `domain/model/CollectionStatus.java` |
| `repository/*` | `adapter/out/persistence/{collection,catalog}/*` |
| `service/CollectionService.java` | `application/service/CollectDatasetService.java` |
| `service/GeocodingService.java` | `application/service/GeocodingService.java` |
| `service/UpsertService.java` | `application/service/UpsertService.java` |
| `config/SeoulApiProperties.java` | `adapter/out/seoulapi/SeoulApiProperties.java` |
| `exception/SeoulApiServerException.java` | `adapter/out/seoulapi/SeoulApiServerException.java` |
| `config/KakaoApiProperties.java` | `adapter/out/kakao/KakaoApiProperties.java` |

→ 사실상 100% 이식 완료. `collector/` 안의 코드는 **현재 시점에서 모두 dead code**.

## 2. 문제점

1. **빌드는 되지만 산출물에 포함되지 않음** — IDE에서 프로젝트로 인식되지만 실제 동작과는 무관한 유령 모듈
2. **중복 코드** — 동일 클래스가 두 위치에 존재. `collector/` 쪽을 잘못 수정해도 빌드는 통과하지만 실제 앱 동작에는 영향 없음 → 함정 코드
3. **신규 작업자 혼란** — "수집 로직은 `collector/`에 있나? `application/`에 있나?" 라우팅 불명확

## 3. 해결 옵션

사용자 의도("프로젝트로 인식되면서 내가 개발/관리할 모듈")를 반영해 살리는 방향으로 옵션을 정리.

### 옵션 A — `collector`를 수집 배치 부트 모듈로 분리 ★ 권장

**컨셉**: `app`은 Web API + 인증 + Chat SSE 전용 부트, `collector`는 일 1회 수집 배치 전용 부트 모듈로 분리.

**근거**:
- 운영 시 수집은 별도 컨테이너/CronJob으로 띄우는 것이 자연스러움 (Web API 트래픽과 격리)
- 마이크로서비스 분리의 자연스러운 첫 단계
- `app`에서 `@Scheduled` 제거 → Web 컨테이너가 모든 인스턴스에서 수집 중복 실행하는 문제 원천 해결 (현재는 단일 인스턴스 가정)

**구조**:
```
app/         # Web API 부트 (controller, security, SSE)
collector/   # 수집 배치 부트 (scheduler, CollectDatasetUseCase 호출)
  ├── src/main/java/.../collector/
  │   ├── CollectorApplication.java   ← 별도 @SpringBootApplication
  │   └── CollectionScheduler.java    ← adapter/in/scheduler에서 이동
  └── src/main/resources/application.yml  ← DB/Kakao/Seoul API만 (Security 불필요)
```

**작업 단계**:
1. `collector/src/main/java/dev/jazzybyte/onseoul/collector/` **전체 삭제** (옛 dead code)
2. `collector/src/test/` 의 옛 테스트 **전체 삭제** (헥사고날 모듈 테스트가 이미 동등 커버)
3. `collector/build.gradle` 의존성 재정의:
   ```gradle
   dependencies {
       implementation project(':adapter')
       implementation project(':application')
       implementation project(':domain')
       implementation project(':common')

       implementation 'org.springframework.boot:spring-boot-starter'
       // Web/Security 의존 없음 — 배치 전용
   }

   bootJar {
       enabled = true
       archiveFileName = 'collector.jar'
       mainClass = 'dev.jazzybyte.onseoul.collector.CollectorApplication'
   }
   jar.enabled = false
   ```
4. `CollectorApplication.java` 신설 — `@SpringBootApplication`, `@EnableScheduling`
5. `adapter/in/scheduler/CollectionScheduler.java` → `collector/`로 이동 (또는 `app`에서는 `@Scheduled` 비활성화하고 `collector` 부트에서만 활성화)
6. `collector/src/main/resources/application.yml` 작성 — DB/Redis(불필요시 제거)/Seoul API/Kakao 키만
7. 검증:
   - `./gradlew :app:bootJar` → `app.jar` 빌드 (수집 스케줄러 미포함)
   - `./gradlew :collector:bootJar` → `collector.jar` 빌드
   - `java -jar collector/build/libs/collector.jar` 단독 실행 확인

**비용**: 중간 (부트 분리 + 스케줄링 위치 이동 + yml 분리)
**효과**: collector 모듈이 살아있는 배포 단위로 부활. 운영/스케일 친화적 구조.

---

### 옵션 B — `collector`를 수집 통합 테스트 전용 모듈로 재정의

**컨셉**: 운영 코드는 헥사고날 그대로 두고, `collector/`는 "수집 시스템 E2E 통합 테스트 + 수동 실행 스크립트"만 담는 모듈로 재정의.

**구조**:
```
collector/
  ├── src/test/java/  ← Testcontainers PostgreSQL + WireMock으로 수집 전체 흐름 검증
  └── src/main/java/  ← 수동 트리거 CLI (선택)
```

**작업 단계**:
1. `collector/src/main/`, `collector/src/test/` 전체 삭제
2. `collector/build.gradle`을 testImplementation 위주로 재구성 (Testcontainers, WireMock)
3. 통합 테스트 1~2건 작성 — 수집 → upsert → change_log 기록까지 전 과정
4. `bootJar.enabled = false`, `jar.enabled = false` (산출물 없음, 테스트 전용)

**비용**: 작음
**효과**: 모듈은 살아있지만 배포 산출물은 없음. "테스트 모듈"이라는 명확한 정체성.

---

### 옵션 C — `collector` 완전 제거

**컨셉**: 사용자 요구("프로젝트로 인식")를 만족하지 않으므로 **비권장**. 참고용으로만 기록.

작업: `settings.gradle`에서 `include 'collector'` 제거 + `collector/` 디렉터리 삭제.

---

## 4. 권장 경로

**옵션 A (배치 부트 모듈 분리)** 를 권장한다.

이유:
1. 사용자 요구("내가 개발/관리할 살아있는 프로젝트")에 정확히 부합 — 배포 산출물(`collector.jar`)이 생기고 운영 단위로 활용
2. 현재 `app` 모듈의 `@Scheduled` 다중 인스턴스 동시 실행 위험을 구조적으로 제거
3. 헥사고날의 `domain/application/adapter`는 변경 없음 — 두 부트(`app`, `collector`)가 동일 코어를 공유
4. Kubernetes/Docker 배포 시 자연스러운 분리 (Web Pod vs CronJob)

## 5. 옵션 A 단계별 체크리스트

- [ ] `collector/src/main/java/dev/jazzybyte/onseoul/collector/` 하위 옛 코드 18개 파일 삭제
- [ ] `collector/src/test/java/dev/jazzybyte/onseoul/collector/` 하위 옛 테스트 9개 파일 삭제
- [ ] `collector/build.gradle` 재작성 (`adapter`, `application` 의존 + `bootJar.enabled = true`)
- [ ] `collector/src/main/java/dev/jazzybyte/onseoul/collector/CollectorApplication.java` 신설
- [ ] `adapter/in/scheduler/CollectionScheduler.java` → `collector/src/main/java/.../collector/CollectionScheduler.java` 로 이동
- [ ] `app`에서 `@EnableScheduling` 제거 (scheduler가 collector로 옮겨졌으므로)
- [ ] `collector/src/main/resources/application.yml` 작성 — DB / Seoul API / Kakao 키
- [ ] `collector/src/main/resources/logback-spring.xml` (선택) — 배치용 로그 포맷
- [ ] `./gradlew :app:bootJar` 정상 빌드
- [ ] `./gradlew :collector:bootJar` 정상 빌드
- [ ] `./gradlew test` 전 모듈 통과
- [ ] `java -jar collector/build/libs/collector.jar` 로컬 실행 시 수집 1회 정상 동작 확인 (수동 트리거 또는 cron `0 0 8 * * *` 대기)
- [ ] README / `docs/api-service-implementation.md`에 "배치 부트 분리" 섹션 추가

## 6. 위험 / 고려사항

- **`@Scheduled` 위치 이동**: `app`에는 더 이상 스케줄러가 없으므로 `app`만 띄우는 환경에서는 수집이 동작하지 않음 → 운영 문서/README에 명시 필요
- **`POST /admin/collection/trigger`** 수동 트리거 엔드포인트는 `app`(Web)에 남기되, `CollectDatasetUseCase`만 호출하면 되므로 변경 불필요
- **테스트 격리**: 통합 테스트는 `app` 또는 `collector` 중 하나에만 두기 — 양쪽 다 띄우면 중복
- **공통 application.yml**: `app`/`collector` 양쪽 yml에 DB 접속 정보가 중복됨 → 환경변수로만 받도록 통일하면 운영상 이슈 없음
