FROM python:3.13-slim

# uv 설치
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 의존성 파일만 먼저 복사 — 레이어 캐시 활용
COPY pyproject.toml uv.lock* ./

# 프로덕션 의존성만 설치 (dev 그룹 제외), 가상환경 없이 시스템에 직접 설치
RUN uv sync --no-dev --no-editable

# 소스 복사
COPY . .

# 보안을 위해 비루트 사용자 사용
RUN addgroup --system jazz && adduser --system jazz --ingroup jazz
USER jazz:jazz

EXPOSE 8000

ENTRYPOINT ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
