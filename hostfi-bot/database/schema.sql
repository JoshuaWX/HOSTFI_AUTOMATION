-- ============================================================
-- HOSTFI Bot — Full Supabase Database Schema
-- Run this in the Supabase SQL Editor to create all tables
-- ============================================================

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id              BIGSERIAL PRIMARY KEY,
    telegram_id     BIGINT UNIQUE NOT NULL,
    username        TEXT,
    first_name      TEXT,
    join_date       TIMESTAMPTZ DEFAULT NOW(),
    is_verified     BOOLEAN DEFAULT FALSE,
    is_banned       BOOLEAN DEFAULT FALSE,
    warn_count      INTEGER DEFAULT 0,
    xp_points       INTEGER DEFAULT 0,
    last_active     TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast lookup by Telegram ID
CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users (telegram_id);

-- Tickets table
CREATE TABLE IF NOT EXISTS tickets (
    id                  UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    ticket_number       SERIAL,
    user_telegram_id    BIGINT NOT NULL,
    issue_description   TEXT NOT NULL,
    status              TEXT DEFAULT 'open'
                            CHECK (status IN ('open', 'claimed', 'resolved', 'closed')),
    assigned_admin_id   BIGINT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    resolved_at         TIMESTAMPTZ,
    rating              INTEGER CHECK (rating BETWEEN 1 AND 5)
);

CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets (status);
CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets (user_telegram_id);

-- Audit logs table
CREATE TABLE IF NOT EXISTS audit_logs (
    id                  BIGSERIAL PRIMARY KEY,
    action              TEXT NOT NULL,
    admin_telegram_id   BIGINT NOT NULL,
    target_telegram_id  BIGINT,
    reason              TEXT,
    metadata            JSONB DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs (action);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs (created_at);

-- Price alerts table
CREATE TABLE IF NOT EXISTS price_alerts (
    id                  BIGSERIAL PRIMARY KEY,
    user_telegram_id    BIGINT NOT NULL,
    coin_id             TEXT NOT NULL,
    target_price        DECIMAL NOT NULL,
    direction           TEXT NOT NULL CHECK (direction IN ('above', 'below')),
    is_active           BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_price_alerts_active ON price_alerts (is_active)
    WHERE is_active = TRUE;

-- Referrals table
CREATE TABLE IF NOT EXISTS referrals (
    id                      BIGSERIAL PRIMARY KEY,
    referrer_telegram_id    BIGINT NOT NULL,
    referred_telegram_id    BIGINT NOT NULL,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals (referrer_telegram_id);

-- DM Conversations table
CREATE TABLE IF NOT EXISTS dm_conversations (
    id                  BIGSERIAL PRIMARY KEY,
    user_telegram_id    BIGINT NOT NULL,
    session_id          TEXT NOT NULL,
    message_role        TEXT NOT NULL CHECK (message_role IN ('user', 'assistant')),
    message_content     TEXT NOT NULL,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dm_conversations_user_session 
    ON dm_conversations (user_telegram_id, session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dm_conversations_session 
    ON dm_conversations (session_id, created_at DESC);
