#!/bin/sh
# OTEL_ENABLED=true 일 때만 OTel Java Agent를 부착한다.
# 기존 JAVA_TOOL_OPTIONS(-Dnetworkaddress.cache.ttl=30)는 여기로 이관해 유실을 막는다.
set -e

JAVA_OPTS="-Dnetworkaddress.cache.ttl=30"

if [ "${OTEL_ENABLED}" = "true" ]; then
  JAVA_OPTS="${JAVA_OPTS} -javaagent:/app/opentelemetry-javaagent.jar"
  echo "[entrypoint] OTel Java Agent 부착됨 (OTEL_ENABLED=true)"
else
  echo "[entrypoint] OTel 비활성 (OTEL_ENABLED=${OTEL_ENABLED:-unset})"
fi

exec java ${JAVA_OPTS} -jar /app/app.jar
