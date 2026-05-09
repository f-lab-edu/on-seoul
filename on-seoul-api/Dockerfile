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

# 보안을 위해 비루트(non-root) 사용자 생성 및 사용
RUN addgroup --system jazz && adduser --system jazz --ingroup jazz
USER jazz:jazz

# Gradle 빌드로 생성된 JAR 파일을 복사
# on-seoul-api/bootstrap/build.gradle 에서 archiveFileName = 'bootstrap.jar'로 설정됨
COPY bootstrap/build/libs/bootstrap.jar app.jar

# 서비스 포트 노출
EXPOSE 8080

# 애플리케이션 실행
ENTRYPOINT ["java", "-jar", "app.jar"]
