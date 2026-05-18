# ADR-0001: 컨텍스트 간 통신 방식

**Status:** Accepted
**Date:** 2026-04-25
**Deciders:** 1인 개발

## Context

4개 바운디드 컨텍스트(`user`, `chat`, `collection`, `notification`)가 단일 Spring Boot 모놀리스에 수직 분리되어 공존한다. BC 간 호출은 같은 JVM 내 메서드 호출이며, 외부 시스템은 두 종류뿐이다:

- **서울 Open API** — `collection`이 호출하는 외부 데이터 소스. 응답 모델이 외부 명명 규칙(예: `MAXCLASSNM`, `AREANM`, `SVCSTATNM`)을 따른다.
- **FastAPI AI 서비스** — `chat`(채팅 스트림)과 `notification`(템플릿 생성)이 각각 호출하는 외부 시스템.

## Decision

**BC 간 통신은 동기 inbound Port 직접 호출**한다. 메시지 브로커, `ApplicationEventPublisher`, Outbox 모두 도입하지 않는다.

**ACL(Anti-Corruption Layer)은 외부 시스템 2종에만** 적용한다. BC 간 ACL은 같은 코드베이스/같은 유비쿼터스 언어이므로 적용하지 않는다.

### 어댑터 배치

```
collection/
  adapter/
    in/scheduler/CollectionScheduler           ← 스케줄러(시스템 진입)
    out/seoulapi/SeoulOpenApiClient            ← 외부 호출
    out/seoulapi/SeoulOpenApiResponseMapper    ← ACL (외부 모델 → 도메인 모델)

chat/
  adapter/
    out/agent/ChatAgentClient                  ← FastAPI 호출
    out/agent/ChatAgentDtoMapper               ← ACL (FastAPI DTO ↔ 도메인)

notification/
  adapter/
    out/agent/TemplateAgentClient              ← FastAPI 호출
    out/agent/TemplateAgentDtoMapper           ← ACL
```

### BC 간 호출 예시

OAuth 로그인 성공 시 기본 구독 생성:

```
user.application.OAuth2SuccessHandler
  → user.application.UserService.register(...)                          // 동일 BC
  → notification.port.in.CreateDefaultSubscriptionsUseCase.create(...)  // BC 경계 (동기)
```

`notification` 측은 inbound port(use case) 인터페이스를 노출하고, `user`는 그 인터페이스에만 의존한다. 구현체 위치는 `notification.application.*`.

## Consequences

**긍정**
- 디버깅 단순: 스택 트레이스 1개로 BC 경계 추적 가능.
- 트랜잭션 경계 명확: 호출 시점에 같은 TX인지 새 TX인지 한 곳에서 결정.
- 메시지 인프라 0: 운영 비용·실패 모드 추가 없음.

**부정**
- 한쪽 BC의 장애가 호출자 BC의 응답 지연으로 직접 전파됨(예: `notification` use case 호출이 지연되면 OAuth 핸들러 응답이 지연). try-catch로 격리 처리.

## Alternatives Considered

**Spring `ApplicationEventPublisher` 즉시 도입**
- 동기 이벤트 리스너는 디버깅만 복잡하게 만들며 분리된 서비스가 없으므로 비동기 이점 미실현.
- **기각.**

**모든 외부/BC 통신을 비동기 이벤트로 통일 (Kafka/RabbitMQ)**
- 메시지 브로커 도입하지 않음. 외부 시스템 통신은 REST API(WebClient) 직접 호출로 처리한다.
- **기각.**
