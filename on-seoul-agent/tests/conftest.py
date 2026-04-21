import os

from dotenv import load_dotenv

# .env를 먼저 로드해서 실제 API 키가 있으면 os.environ에 반영한다.
# setdefault는 키가 없을 때만 설정하므로 실제 키를 덮어쓰지 않는다.
load_dotenv()

# 단위 테스트 폴백 — 실제 키가 없어도 Settings() 초기화가 통과되도록
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
