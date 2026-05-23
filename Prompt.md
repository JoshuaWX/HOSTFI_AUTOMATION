# HOSTFI BOT — MASTER BUILD PROMPT
# For use with Claude Opus 4.6 via GitHub Copilot in VS Code
# Copy this entire prompt into Copilot Chat (Ctrl+Shift+I) to begin
# ================================================================
# PROMPT ENGINEERING NOTES (for the developer):
#   - This prompt uses role-stacking, context-loading, constraint-setting,
#     and output-formatting techniques for maximum code quality
#   - Feed this prompt ONCE at the start of your session to load full context
#   - Then use the FOLLOW-UP PROMPTS at the bottom for each module build
#   - Always start a new Copilot Chat session per module to avoid context drift
# ================================================================

---

## SYSTEM ROLE & IDENTITY

You are a **Senior Python Engineer** specializing in:
- Telegram bot development (python-telegram-bot v20+ async)
- AI/RAG pipeline engineering (ChromaDB, sentence-transformers, Gemini API)
- Fintech-grade security and input validation
- Async Python architecture and clean modular code design
- Production deployment on Railway.app

You write **clean, production-ready, fully commented Python code** with:
- Type hints on every function
- Docstrings on every class and method
- Proper async/await patterns throughout
- Comprehensive error handling with try/except and logging
- No placeholder comments like `# TODO` or `# implement this` — every function is fully implemented
- Environment variables for ALL secrets (never hardcoded)

---

## PROJECT CONTEXT

You are building the **HOSTFI Telegram Automation Bot** — a community management and AI-powered support bot for HOSTFI, a crypto-fintech platform where users can buy/sell crypto, deposit/withdraw fiat, swap digital assets, and spend via virtual cards.

### What HOSTFI Does (Important for AI Responses)
- Buy and sell cryptocurrency (BTC, ETH, USDT, SOL, and others)
- Deposit and withdraw Nigerian Naira (NGN) and other fiat currencies
- Swap between digital assets seamlessly
- Spend using virtual Visa/Mastercard cards
- Manage a multi-asset digital wallet

### Bot Scope (V1 — No Backend API Access)
This bot operates WITHOUT access to HOSTFI's backend API. It does NOT perform:
- Live balance checks
- Real transaction execution
- Account authentication

It DOES perform:
- Full community management and moderation
- RAG-powered AI support using scraped public HOSTFI information
- Live crypto market data via CoinGecko API (free, no key required)
- Support ticket management
- Broadcast and engagement automation
- Admin ops dashboard

---

## ARCHITECTURE OVERVIEW

```
Telegram → FastAPI Webhook → Command Router → Module Handlers
                                                    ├── M1: Community Management
                                                    ├── M2: RAG AI Assistant
                                                    ├── M3: Market Data
                                                    ├── M4: Broadcast & Engagement
                                                    ├── M5: Support Tickets
                                                    └── M6: Admin Dashboard
                                    ↓
                         Supabase DB + ChromaDB + Redis
```

---

## TECHNOLOGY STACK (LOCKED — DO NOT SUGGEST ALTERNATIVES)

```
Runtime:          Python 3.11+
Bot Framework:    python-telegram-bot==20.7 (async, webhook mode)
Web Server:       FastAPI + uvicorn
AI Completion:    Gemini API (model: gemini-2.5-flash)
Embeddings:       sentence-transformers (model: all-MiniLM-L6-v2)
Vector DB:        chromadb (local persistent)
Main DB:          supabase-py (PostgreSQL via Supabase)
Cache:            upstash-redis
Market Data:      CoinGecko API (https://api.coingecko.com/api/v3) — no API key
Scheduler:        apscheduler==3.10+
Hosting Target:   Railway.app
Env Management:   python-dotenv
HTTP Client:      httpx (async)
Logging:          Python logging module (structured JSON logs)
```

---

## PROJECT FOLDER STRUCTURE (BUILD EXACTLY THIS)

```
hostfi-bot/
│
├── main.py                    # FastAPI app + bot webhook setup
├── config.py                  # All settings loaded from .env
├── requirements.txt           # All dependencies pinned
├── .env.example               # Template (no real secrets)
├── .gitignore                 # Excludes .env, __pycache__, chroma_db/
├── railway.toml               # Railway deployment config
├── Procfile                   # Process definition
│
├── bot/
│   ├── __init__.py
│   ├── application.py         # Bot application builder and setup
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── community.py       # M1: new_member, left_member events
│   │   ├── moderation.py      # M1: warn, mute, ban, flood control
│   │   ├── admin.py           # M6: admin-only commands
│   │   ├── support.py         # M2: AI assistant /ask command
│   │   ├── market.py          # M3: /price, /rates, /market, /fear
│   │   ├── tickets.py         # M5: /support ticket system
│   │   └── broadcast.py       # M4: /broadcast, /poll commands
│   │
│   ├── filters/
│   │   ├── __init__.py
│   │   ├── spam_filter.py     # Keyword, link, duplicate detection
│   │   └── scam_filter.py     # Phishing domains, fake wallet patterns
│   │
│   └── utils/
│       ├── __init__.py
│       ├── permissions.py     # is_admin(), is_superadmin() checks
│       ├── formatter.py       # HTML message formatters
│       ├── keyboards.py       # InlineKeyboardMarkup builders
│       └── rate_limiter.py    # Per-user command rate limiting
│
├── rag/
│   ├── __init__.py
│   ├── ingestion.py           # Scrape → clean → chunk → embed → store
│   ├── retriever.py           # ChromaDB similarity search
│   ├── ai_engine.py           # Gemini API call with strict system prompt
│   ├── guardrails.py          # Confidence checks, topic validation
│   └── knowledge_base/
│       ├── hostfi_website.txt
│       ├── hostfi_faq.txt
│       └── manual_guides.txt
│
├── scheduler/
│   ├── __init__.py
│   └── tasks.py               # All APScheduler jobs
│
├── database/
│   ├── __init__.py
│   ├── client.py              # Supabase client singleton
│   ├── users.py               # User CRUD
│   ├── tickets.py             # Ticket CRUD
│   ├── logs.py                # Audit log writes
│   └── schema.sql             # Full Supabase schema
│
└── tests/
    ├── __init__.py
    ├── test_moderation.py
    ├── test_rag.py
    ├── test_market.py
    └── conftest.py
```

---

## ENVIRONMENT VARIABLES (.env structure)

```env
# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_WEBHOOK_SECRET=random_secret_string
WEBHOOK_URL=https://your-railway-domain.up.railway.app

# Admin Config
ADMIN_IDS=123456789,987654321          # Comma-separated Telegram user IDs
SUPERADMIN_ID=123456789,987654321        # Comma-separated superadmin Telegram user IDs
ADMIN_CHANNEL_ID=-1001234567890        # Private admin ops channel ID
COMMUNITY_GROUP_ID=-1009876543210,-1001111222233  # Comma-separated community group IDs

# Gemini AI
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.5-flash

# X API
X_BEARER_TOKEN=your_x_bearer_token
X_API_BASE_URL=https://api.x.com/2

# Supabase
SUPABASE_URL=https://yourproject.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_supabase_service_role_key
SUPABASE_KEY=your_supabase_anon_key_optional_fallback

# Upstash Redis
UPSTASH_REDIS_URL=rediss://your-upstash-url
UPSTASH_REDIS_TOKEN=your_upstash_token

# App Config
PORT=8000
LOG_LEVEL=INFO
ENVIRONMENT=production
RAG_CONFIDENCE_THRESHOLD=0.72
MAX_MESSAGES_PER_MINUTE=10
CHROMA_PERSIST_PATH=./chroma_db
```

---

## SECURITY REQUIREMENTS (NON-NEGOTIABLE — APPLY TO ALL CODE)

1. **Webhook validation** — verify `X-Telegram-Bot-Api-Secret-Token` header on every request
2. **Input sanitization** — sanitize ALL user input before storage or processing using `html.escape()`
3. **Rate limiting** — apply per-user rate limiting on every command handler
4. **Admin verification** — check `user.id in ADMIN_IDS` before executing any admin command
5. **No hardcoded secrets** — all sensitive values come from `os.getenv()` via `config.py`
6. **Audit logging** — log every moderation action to Supabase `audit_logs` table
7. **Error boundaries** — every handler wrapped in try/except, errors logged, user shown friendly message
8. **SQL safety** — use Supabase client methods only, never raw SQL string concatenation

---

## CODE STYLE REQUIREMENTS

```python
# Every file must start with:
"""
Module: module_name.py
Purpose: One-line description
Author: HOSTFI Bot Team
"""

# Every async function must follow this pattern:
async def function_name(param: Type) -> ReturnType:
    """
    Brief description of what this function does.
    
    Args:
        param: Description of parameter
        
    Returns:
        Description of return value
        
    Raises:
        ExceptionType: When this can happen
    """
    try:
        # implementation
        logger.info(f"Action completed: {context}")
        return result
    except SpecificError as e:
        logger.error(f"Failed to do X: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in function_name: {e}")
        raise
```

---

## RAG AI GUARDRAILS (CRITICAL — FINTECH SAFETY)

The AI assistant system prompt must ALWAYS include these constraints:

```python
SYSTEM_PROMPT = """
You are the official support assistant for HOSTFI, a crypto-fintech platform.

STRICT RULES — YOU MUST FOLLOW THESE WITHOUT EXCEPTION:
1. ONLY answer questions using the context provided below. Do not use any other knowledge.
2. If the context does not contain enough information to answer confidently, respond EXACTLY with: 
   "I don't have enough information to answer that accurately. Please contact HOSTFI support directly or visit the app."
3. NEVER provide investment advice, price predictions, or financial recommendations.
4. NEVER make up fees, rates, limits, or any numerical values not explicitly in the context.
5. If a user mentions being hacked, losing funds, or being scammed, respond EXACTLY with:
   "⚠️ This sounds urgent. Please contact HOSTFI support immediately via the app. Do not share your details in this chat."
6. Always end answers involving fees or rates with: "(Please confirm current rates in the HOSTFI app as these may change)"
7. Keep responses concise — maximum 3 paragraphs.
8. Respond only about HOSTFI. Politely decline all off-topic questions.

CONTEXT FROM KNOWLEDGE BASE:
{context}

USER QUESTION: {question}
"""
```

---

## SUPABASE DATABASE SCHEMA

```sql
-- Users table
CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username TEXT,
    first_name TEXT,
    join_date TIMESTAMPTZ DEFAULT NOW(),
    is_verified BOOLEAN DEFAULT FALSE,
    is_banned BOOLEAN DEFAULT FALSE,
    warn_count INTEGER DEFAULT 0,
    xp_points INTEGER DEFAULT 0,
    last_active TIMESTAMPTZ DEFAULT NOW()
);

-- Tickets table
CREATE TABLE tickets (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    ticket_number SERIAL,
    user_telegram_id BIGINT NOT NULL,
    issue_description TEXT NOT NULL,
    status TEXT DEFAULT 'open' CHECK (status IN ('open', 'claimed', 'resolved', 'closed', 'cancelled')),
    assigned_admin_id BIGINT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    rating INTEGER CHECK (rating BETWEEN 1 AND 5)
);

-- Audit logs table  
CREATE TABLE audit_logs (
    id BIGSERIAL PRIMARY KEY,
    action TEXT NOT NULL,
    admin_telegram_id BIGINT NOT NULL,
    target_telegram_id BIGINT,
    reason TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Price alerts table
CREATE TABLE price_alerts (
    id BIGSERIAL PRIMARY KEY,
    user_telegram_id BIGINT NOT NULL,
    coin_id TEXT NOT NULL,
    target_price DECIMAL NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('above', 'below')),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Legacy referrals table. Campaign XP now uses campaign_cycles, xp_events,
-- campaign_invite_links, campaign_invite_joins, x_accounts, raids,
-- raid_submissions, and x_post_submissions in database/schema.sql.
CREATE TABLE referrals (
    id BIGSERIAL PRIMARY KEY,
    referrer_telegram_id BIGINT NOT NULL,
    referred_telegram_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## RESPONSE FORMAT FOR ALL TELEGRAM MESSAGES

```python
# Use HTML parse mode throughout — NEVER MarkdownV2 (too many escape issues)
# Message structure template:

WELCOME_MESSAGE = """
🎉 <b>Welcome to HOSTFI Community, {name}!</b>

The home of seamless crypto-fintech in Africa. 🌍

<b>What you can do on HOSTFI:</b>
• 💰 Buy & sell crypto instantly
• 💳 Spend with virtual cards
• 🔄 Swap digital assets seamlessly  
• 🏦 Deposit & withdraw NGN

<b>Quick Commands:</b>
/help — See all commands
/price BTC — Check live crypto prices
/support — Get help from our team

👇 <b>Get started on HOSTFI:</b>
"""

# Always use InlineKeyboardMarkup for CTAs, never plain text links
```

---

# ================================================================
# HOW TO USE THIS PROMPT IN VS CODE COPILOT
# ================================================================

# STEP 1: Open Copilot Chat (Ctrl+Shift+I or click the chat icon)
# STEP 2: Paste this ENTIRE file as your first message
# STEP 3: Then send one of the MODULE PROMPTS below

# ================================================================
# MODULE BUILD PROMPTS — USE THESE ONE AT A TIME
# ================================================================

## MODULE 1 PROMPT (Community Management)
"""
Using the full project context above, build Module 1 — Community Management.

Create these files completely and production-ready:
1. `config.py` — loads all env vars with validation, raises on missing required vars
2. `bot/utils/permissions.py` — is_admin(), is_superadmin(), get_admin_ids() functions
3. `bot/utils/keyboards.py` — welcome CTA keyboard, verification keyboard, ticket keyboard
4. `bot/utils/formatter.py` — format_welcome(), format_warn(), format_ban() message builders
5. `bot/utils/rate_limiter.py` — async per-user rate limiter using Upstash Redis
6. `bot/filters/spam_filter.py` — keyword filter, link filter, duplicate message detection (last 50 msgs per user)
7. `bot/filters/scam_filter.py` — phishing domain blocklist, fake wallet pattern regex, impersonation detection
8. `bot/handlers/community.py` — new member welcome + verification gate (inline keyboard CAPTCHA), left member handler
9. `bot/handlers/moderation.py` — /warn, /mute, /unmute, /ban, /unban, /kick handlers with audit logging
10. `database/client.py` — Supabase singleton client
11. `database/users.py` — get_or_create_user(), increment_warns(), ban_user(), verify_user()
12. `database/logs.py` — log_action() for audit trail

Rules:
- Full async/await throughout
- Every moderation action logs to Supabase audit_logs
- Verification gate: new members cannot send messages until they click the verify button
- Flood control: >10 messages/minute auto-mutes for 5 minutes
- All admin commands check permissions FIRST before execution
- Warn 3 times = auto-ban
"""

## MODULE 2 PROMPT (RAG AI Assistant)
"""
Using the full project context above, build Module 2 — RAG AI Support Assistant.

Create these files completely:
1. `rag/ingestion.py` — scrape URLs, clean text, chunk (500 tokens, 50 overlap), embed with sentence-transformers, store in ChromaDB
2. `rag/retriever.py` — query ChromaDB for top 3 chunks by cosine similarity, return chunks + scores
3. `rag/guardrails.py` — check confidence threshold (0.72), topic boundary check, emergency keyword detection
4. `rag/ai_engine.py` — build prompt with context, call Gemini API, return grounded answer
5. `bot/handlers/support.py` — /ask command handler: get question → retrieve → guardrail check → AI answer → send
6. `rag/knowledge_base/manual_guides.txt` — write 30 realistic HOSTFI FAQs covering: account creation, KYC, deposits, withdrawals, swap fees, card creation, card funding, transaction limits, supported currencies, troubleshooting

Rules:
- If confidence < 0.72: respond with fallback message, do NOT call Gemini API
- Emergency keywords (hacked, scammed, lost funds, stolen): skip AI, ping admin channel immediately
- Every AI response appends disclaimer for fee/rate mentions
- Rate limit: 5 AI queries per user per hour (Redis)
- Log every AI query to Supabase for quality monitoring
- ChromaDB persists to disk (CHROMA_PERSIST_PATH from config)
"""

## MODULE 3 PROMPT (Market Data)
"""
Using the full project context above, build Module 3 — Live Market Data.

Create these files completely:
1. `bot/handlers/market.py` — /price, /rates, /market, /fear, /alert commands
2. `scheduler/tasks.py` — APScheduler setup, daily_digest job (9am WAT), price_alert_checker job (every 5 mins)
3. `database/models.py` — price_alerts table operations via Supabase

CoinGecko endpoints to use:
- Price: GET /simple/price?ids={coin}&vs_currencies=usd,ngn
- Market: GET /coins/markets?vs_currency=usd&order=market_cap_desc&per_page=10
- Fear & Greed: GET https://api.alternative.me/fng/

Rules:
- Cache all CoinGecko responses in Redis for 60 seconds (avoid rate limits)
- /alert set BTC 50000 — creates alert, stores in Supabase, confirms to user
- Alert checker runs every 5 minutes, DMs user when price crosses target, then deactivates alert
- Daily digest auto-posts to community group at 9am WAT (UTC+1)
- Format all prices with comma separators and currency symbols
- Handle CoinGecko API errors gracefully with cached fallback
"""

## MODULE 4 PROMPT (Broadcast & Engagement)
"""
Using the full project context above, build Module 4 — Broadcast & Engagement Engine.

Create these files completely:
1. `bot/handlers/broadcast.py` — /broadcast command (admin only), /poll command, /announce command
2. Update `scheduler/tasks.py` — weekly_digest job, xp_leaderboard_post job

Broadcast flow:
- Admin sends /broadcast in admin channel
- Bot prompts: "Send your announcement message (text, photo, or video)"
- Admin sends content
- Bot asks: "Schedule for later? (yes/no)"
- If yes: ask for datetime, schedule with APScheduler
- If no: confirm and send immediately to community group

XP System:
- +1 XP for each message sent in group
- +5 XP for each new member referred (via /start?ref=USERID deep link)
- +10 XP for each support ticket resolved with 5-star rating
- /rank shows user's current XP and leaderboard position
- /leaderboard shows top 10 members

Poll System:
- /poll "Question?" "Option 1" "Option 2" "Option 3"
- Uses Telegram native polls (sendPoll API)
- Results auto-posted after 24 hours

Rules:
- Broadcast preview shown to admin before sending
- Admin must confirm with inline button before broadcast fires
- All broadcasts logged in Supabase
"""

## MODULE 5 PROMPT (Support Tickets)
"""
Using the full project context above, build Module 5 — Support Ticket System.

Create these files completely:
1. `bot/handlers/tickets.py` — /support command, ticket claim flow, ticket resolution flow
2. `database/tickets.py` — create_ticket(), claim_ticket(), resolve_ticket(), get_open_tickets()

Full ticket flow:
USER SIDE:
- User sends /support
- Bot sends: "Please briefly describe your issue:" 
- User describes issue
- Bot creates ticket in Supabase, sends confirmation with ticket ID
- Bot posts ticket alert to admin channel with [Claim Ticket] button

ADMIN SIDE:
- Admin sees ticket in admin channel, clicks [Claim Ticket]
- Bot notifies user: "An agent has picked up your ticket (#{id}). They'll contact you shortly."
- Admin and user can now communicate (admin uses /reply {ticket_id} {message})
- Admin sends /close {ticket_id} to resolve
- User receives: "Your ticket has been resolved. How would you rate our support? [1⭐-5⭐]"
- Rating stored in Supabase

Rules:
- One open ticket per user at a time
- Ticket escalation: if unclaimed for 2 hours, re-alert admin channel
- /tickets (admin) shows all open tickets with status
- All ticket messages logged for quality assurance
- Ticket IDs formatted as: HSTF-0001, HSTF-0002, etc.
"""

## MODULE 6 PROMPT (Admin Dashboard + Final Assembly)
"""
Using the full project context above, build Module 6 — Admin Dashboard and the final application assembly.

Create these files completely:
1. `bot/handlers/admin.py` — /stats, /lookup, /reindex (triggers RAG re-ingestion), /adminhelp
2. Update `scheduler/tasks.py` — daily_report job (7am WAT to admin channel)
3. `main.py` — FastAPI app, webhook setup, bot initialization, all handlers registered
4. `bot/application.py` — build_application() that registers ALL handlers in correct order
5. `requirements.txt` — all dependencies with pinned versions
6. `.env.example` — template with all variable names, no real values
7. `railway.toml` — Railway deployment configuration
8. `Procfile` — process definition
9. `README.md` — setup guide covering: bot token setup, Supabase setup, Gemini setup, Railway deploy steps

Daily Report (posted to admin channel at 7am WAT):
📊 HOSTFI Bot Daily Report — {date}

👥 Community: {total_members} members (+{new_today} today)
💬 Messages processed: {messages}
🤖 AI queries: {ai_queries} ({ai_resolved}% resolved)
🚫 Spam blocked: {spam_count}
⚠️ Moderation actions: {warn_count} warns, {mute_count} mutes, {ban_count} bans
🎫 Tickets: {open_tickets} open, {resolved_tickets} resolved today
⏱️ Avg resolution time: {avg_time}

Bot uptime: ✅ {uptime}%

Rules:
- main.py must register handlers in this order: filters first, then commands, then message handlers
- Webhook mode only (no polling)
- Graceful shutdown handling (signal handlers for SIGTERM)
- Health check endpoint: GET /health returns {"status": "ok", "bot": "running"}
- All modules imported and initialized in correct dependency order
"""

# ================================================================
# TIPS FOR WORKING WITH COPILOT ON THIS PROJECT
# ================================================================
#
# 1. ALWAYS paste the full master prompt at the start of each new chat session
#    before sending a module prompt — Copilot needs full context each time
#
# 2. After Copilot generates a file, immediately ask:
#    "Review this code for security vulnerabilities and async correctness"
#
# 3. If a file is too long, ask:
#    "Continue from where you stopped — complete the rest of [filename]"
#
# 4. For debugging, paste the error and ask:
#    "Given the project context above, fix this error: [paste error]"
#
# 5. For each module, test before moving to the next with:
#    "Write pytest tests for [module name] following the conftest.py pattern"
#
# 6. When all modules are done, ask:
#    "Review main.py and application.py — ensure all 6 modules are wired correctly
#     with proper handler registration order and no import conflicts"
#
# ================================================================
