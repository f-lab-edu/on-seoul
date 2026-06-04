-- OAuth 이메일 충돌 IT용 users 스키마 (실제 PostgreSQL).
-- uq_users_email_hash 유니크 제약을 재현해 23505(중복 위반)를 발생시킨다.
CREATE TABLE IF NOT EXISTS users (
    id          BIGSERIAL PRIMARY KEY,
    provider    VARCHAR(20)  NOT NULL,
    provider_id VARCHAR(100) NOT NULL,
    email_enc   TEXT,
    email_hash  VARCHAR(64),
    nickname    VARCHAR(100),
    phone_enc   TEXT,
    phone_hash  VARCHAR(64),
    status      VARCHAR(20)  NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_hash ON users (email_hash);
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_provider ON users (provider, provider_id);
