FROM python:3.13-slim

# uv 설치 — latest 대신 버전 고정 권장 (ghcr.io/astral-sh/uv:x.y.z)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 의존성 파일만 먼저 복사 — 레이어 캐시 활용
COPY pyproject.toml uv.lock ./

# BuildKit 캐시 마운트로 uv 다운로드 캐시 재사용
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-editable

# 소스 복사
COPY . .

RUN addgroup --system jazz \
 && adduser --system --ingroup jazz --home /app jazz \
 && chown -R jazz:jazz /app

USER jazz:jazz

EXPOSE 8000

ENTRYPOINT ["/app/.venv/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
