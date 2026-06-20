# 실행 스테이지 전용 Dockerfile
# 빌드는 GitHub Actions와 같은 CI 환경에서 사전에 완료된 것을 가정합니다.
FROM eclipse-temurin:21-jre-jammy
WORKDIR /app

# 필요한 툴 설치 (트러블슈팅용 최소 툴)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    telnet \
    netcat-openbsd \
    dnsutils \
    procps \
    net-tools \
    jq \
    && rm -rf /var/lib/apt/lists/*

# OTel Java Agent 베이크 (root 단계에서 다운로드, 읽기권한 보장)
ARG OTEL_AGENT_VERSION=2.11.0
RUN wget -O /app/opentelemetry-javaagent.jar \
      https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v${OTEL_AGENT_VERSION}/opentelemetry-javaagent.jar \
    && chmod 0644 /app/opentelemetry-javaagent.jar

# 토글 entrypoint
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod 0755 /app/entrypoint.sh

# 보안을 위해 비루트(non-root) 사용자 생성 및 사용
RUN addgroup --system jazz && adduser --system jazz --ingroup jazz
USER jazz:jazz

# Gradle 빌드로 생성된 JAR 파일을 복사
# on-seoul-api/bootstrap/build.gradle 에서 archiveFileName = 'bootstrap.jar'로 설정됨
COPY bootstrap/build/libs/bootstrap.jar app.jar

# 서비스 포트 노출
EXPOSE 8080

# 애플리케이션 실행
# JVM DNS 캐시 TTL(-Dnetworkaddress.cache.ttl=30)과 OTel Agent 토글은 entrypoint.sh로 이관됨.
ENTRYPOINT ["/app/entrypoint.sh"]
