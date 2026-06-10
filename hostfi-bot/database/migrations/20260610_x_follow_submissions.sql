CREATE TABLE IF NOT EXISTS public.x_follow_submissions (
    id                      BIGSERIAL PRIMARY KEY,
    cycle_id                 BIGINT NOT NULL REFERENCES public.campaign_cycles(id),
    invite_join_id           BIGINT NOT NULL REFERENCES public.campaign_invite_joins(id),
    referrer_telegram_id     BIGINT NOT NULL,
    referee_telegram_id      BIGINT NOT NULL,
    x_account_id             BIGINT REFERENCES public.x_accounts(id),
    x_username               TEXT NOT NULL,
    proof_file_id            TEXT NOT NULL,
    proof_file_unique_id     TEXT,
    proof_content_type       TEXT NOT NULL DEFAULT 'photo',
    status                   TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'approved', 'rejected')),
    reviewed_by              BIGINT,
    reviewed_at              TIMESTAMPTZ,
    awarded_at               TIMESTAMPTZ,
    referrer_xp_awarded      INTEGER DEFAULT 0,
    referee_xp_awarded       INTEGER DEFAULT 0,
    metadata                 JSONB DEFAULT '{}'::jsonb,
    created_at               TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_x_follow_submissions_status
    ON public.x_follow_submissions (status, created_at);

CREATE INDEX IF NOT EXISTS idx_x_follow_submissions_referrer
    ON public.x_follow_submissions (cycle_id, referrer_telegram_id);

CREATE INDEX IF NOT EXISTS idx_x_follow_submissions_referee
    ON public.x_follow_submissions (referee_telegram_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_x_follow_submissions_active_referee
    ON public.x_follow_submissions (referee_telegram_id)
    WHERE status IN ('pending', 'approved');

CREATE UNIQUE INDEX IF NOT EXISTS idx_x_follow_submissions_active_x_account
    ON public.x_follow_submissions (x_account_id)
    WHERE x_account_id IS NOT NULL AND status IN ('pending', 'approved');
