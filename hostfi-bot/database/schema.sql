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
                            CHECK (status IN ('open', 'claimed', 'resolved', 'closed', 'cancelled')),
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

-- Campaign cycles table
CREATE TABLE IF NOT EXISTS campaign_cycles (
    id              BIGSERIAL PRIMARY KEY,
    cycle_number    INTEGER UNIQUE NOT NULL,
    status          TEXT DEFAULT 'active'
                        CHECK (status IN ('active', 'finished')),
    start_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    end_at          TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    started_by      BIGINT,
    finished_by     BIGINT,
    reward_config   JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_campaign_cycles_status ON campaign_cycles (status);

-- Campaign XP ledger table
CREATE TABLE IF NOT EXISTS xp_events (
    id                  BIGSERIAL PRIMARY KEY,
    cycle_id             BIGINT NOT NULL REFERENCES campaign_cycles(id),
    telegram_id          BIGINT NOT NULL,
    amount               INTEGER NOT NULL,
    event_type           TEXT NOT NULL,
    status               TEXT DEFAULT 'approved'
                            CHECK (status IN ('approved', 'rejected', 'pending')),
    reason               TEXT,
    evidence_url         TEXT,
    external_id          TEXT,
    actor_telegram_id    BIGINT DEFAULT 0,
    approved_at          TIMESTAMPTZ,
    metadata             JSONB DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_xp_events_cycle_user ON xp_events (cycle_id, telegram_id);
CREATE INDEX IF NOT EXISTS idx_xp_events_type ON xp_events (event_type);
CREATE INDEX IF NOT EXISTS idx_xp_events_approved ON xp_events (cycle_id, status, approved_at);

-- Telegram campaign invite links
CREATE TABLE IF NOT EXISTS campaign_invite_links (
    id                      BIGSERIAL PRIMARY KEY,
    cycle_id                 BIGINT NOT NULL REFERENCES campaign_cycles(id),
    inviter_telegram_id      BIGINT NOT NULL,
    invite_link              TEXT UNIQUE NOT NULL,
    is_active                BOOLEAN DEFAULT TRUE,
    created_at               TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (cycle_id, inviter_telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_campaign_invite_links_inviter
    ON campaign_invite_links (inviter_telegram_id);

-- Telegram campaign invite joins
CREATE TABLE IF NOT EXISTS campaign_invite_joins (
    id                      BIGSERIAL PRIMARY KEY,
    cycle_id                 BIGINT NOT NULL REFERENCES campaign_cycles(id),
    inviter_telegram_id      BIGINT NOT NULL,
    invitee_telegram_id      BIGINT NOT NULL,
    invite_link              TEXT NOT NULL,
    joined_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    eligible_at              TIMESTAMPTZ NOT NULL,
    awarded_at               TIMESTAMPTZ,
    status                   TEXT DEFAULT 'pending'
                                CHECK (status IN ('pending', 'awarded', 'ineligible')),
    metadata                 JSONB DEFAULT '{}'::jsonb,
    UNIQUE (cycle_id, invitee_telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_campaign_invite_joins_pending
    ON campaign_invite_joins (status, eligible_at);

-- Linked X accounts
CREATE TABLE IF NOT EXISTS x_accounts (
    id                      BIGSERIAL PRIMARY KEY,
    telegram_id              BIGINT UNIQUE NOT NULL,
    x_user_id                TEXT,
    username                 TEXT NOT NULL,
    verification_code        TEXT,
    verification_post_id     TEXT,
    status                   TEXT DEFAULT 'pending'
                                CHECK (status IN ('pending', 'verified')),
    verified_at              TIMESTAMPTZ,
    updated_at               TIMESTAMPTZ DEFAULT NOW(),
    created_at               TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_x_accounts_username ON x_accounts (username);

-- Approved raid targets
CREATE TABLE IF NOT EXISTS raids (
    id              BIGSERIAL PRIMARY KEY,
    cycle_id         BIGINT NOT NULL REFERENCES campaign_cycles(id),
    created_by       BIGINT NOT NULL,
    target_post_id   TEXT NOT NULL,
    target_url       TEXT NOT NULL,
    deadline_at      TIMESTAMPTZ NOT NULL,
    status           TEXT DEFAULT 'active'
                        CHECK (status IN ('active', 'closed')),
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raids_cycle_status ON raids (cycle_id, status);

-- Raid proof submissions
CREATE TABLE IF NOT EXISTS raid_submissions (
    id              BIGSERIAL PRIMARY KEY,
    raid_id          BIGINT NOT NULL REFERENCES raids(id),
    cycle_id         BIGINT NOT NULL REFERENCES campaign_cycles(id),
    telegram_id      BIGINT NOT NULL,
    x_post_id        TEXT NOT NULL,
    proof_url        TEXT NOT NULL,
    status           TEXT DEFAULT 'approved'
                        CHECK (status IN ('approved', 'rejected', 'pending')),
    awarded_at       TIMESTAMPTZ,
    metadata         JSONB DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (raid_id, telegram_id),
    UNIQUE (x_post_id)
);

CREATE INDEX IF NOT EXISTS idx_raid_submissions_user ON raid_submissions (telegram_id);

-- Personal HostFi X post submissions
CREATE TABLE IF NOT EXISTS x_post_submissions (
    id                  BIGSERIAL PRIMARY KEY,
    cycle_id             BIGINT NOT NULL REFERENCES campaign_cycles(id),
    telegram_id          BIGINT NOT NULL,
    x_post_id            TEXT UNIQUE NOT NULL,
    proof_url            TEXT NOT NULL,
    submission_date      DATE NOT NULL,
    status               TEXT DEFAULT 'approved'
                            CHECK (status IN ('approved', 'rejected', 'pending')),
    awarded_at           TIMESTAMPTZ,
    metadata             JSONB DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (cycle_id, telegram_id, submission_date)
);

CREATE INDEX IF NOT EXISTS idx_x_post_submissions_user
    ON x_post_submissions (cycle_id, telegram_id, submission_date);
