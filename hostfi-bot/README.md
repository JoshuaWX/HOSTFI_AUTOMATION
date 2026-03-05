# HOSTFI Telegram Bot

Community management, AI-powered support, and crypto market data bot for HOSTFI.

---

## Features

- **Community Management** — Welcome flow with math CAPTCHA, spam/scam detection, flood control
- **AI Support** — RAG-powered Q&A using HOSTFI knowledge base (Groq + ChromaDB)
- **Live Market Data** — Crypto prices, market overview, Fear & Greed Index, price alerts
- **Broadcast & Engagement** — Admin broadcasts, native polls, XP system, referral tracking
- **Support Tickets** — Full ticket lifecycle with claim, reply, close, and rating
- **Admin Dashboard** — Stats, user lookup, knowledge base re-indexing, daily reports

---

## Tech Stack

| Component | Technology |
|---|---|
| Bot Framework | python-telegram-bot 20.7 (async, webhook) |
| Web Server | FastAPI + uvicorn |
| AI | Groq API (llama-3.3-70b-versatile) |
| Embeddings | sentence-transformers (all-MiniLM-L6-v2) |
| Vector DB | ChromaDB (local persistent) |
| Database | Supabase (PostgreSQL) |
| Cache | Upstash Redis |
| Market Data | CoinGecko API (free) |
| Scheduler | APScheduler 3.10 |
| Hosting | Railway.app |

---

## Setup Guide

### 1. Prerequisites

- Python 3.11+
- A Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Supabase project (free tier works)
- Groq API key (from [console.groq.com](https://console.groq.com))
- Upstash Redis instance (from [upstash.com](https://upstash.com))

### 2. Clone and Install

```bash
git clone <your-repo-url>
cd hostfi-bot
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Environment Variables

Copy the example env file and fill in your values:

```bash
cp .env.example .env
```

Edit `.env` with your actual credentials:

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `TELEGRAM_WEBHOOK_SECRET` | Random secret string for webhook verification |
| `WEBHOOK_URL` | Your public URL (e.g. `https://your-app.up.railway.app`) |
| `ADMIN_IDS` | Comma-separated Telegram user IDs for admins |
| `SUPERADMIN_ID` | Primary admin Telegram user ID |
| `ADMIN_CHANNEL_ID` | Private admin channel ID (starts with `-100`) |
| `COMMUNITY_GROUP_ID` | Community group ID (starts with `-100`) |
| `GROQ_API_KEY` | Groq API key |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase anon/public key |
| `UPSTASH_REDIS_URL` | Upstash Redis URL |
| `UPSTASH_REDIS_TOKEN` | Upstash Redis token |

### 4. Supabase Setup

1. Create a new Supabase project at [supabase.com](https://supabase.com)
2. Go to **SQL Editor** and run the full schema from `database/schema.sql`
3. Copy your project URL and anon key into `.env`

### 5. Groq Setup

1. Sign up at [console.groq.com](https://console.groq.com)
2. Create an API key
3. Add it to `.env` as `GROQ_API_KEY`

### 6. Bot Token Setup

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the prompts
3. Copy the token to `.env` as `TELEGRAM_BOT_TOKEN`
4. Send `/setcommands` to BotFather and paste:
   ```
   start - Start the bot
   help - Show all commands
   rules - Community rules
   price - Live crypto price
   rates - Exchange rates
   market - Market overview
   fear - Fear & Greed Index
   alert - Set price alerts
   ask - Ask the AI assistant
   support - Open a support ticket
   rank - Your XP and rank
   leaderboard - Top 10 members
   ```

### 7. Run Locally

```bash
# You'll need a public URL for webhooks — use ngrok for local dev:
# ngrok http 8000
# Then set WEBHOOK_URL to the ngrok URL in .env

python main.py
```

---

## Railway Deployment

### 1. Push to GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin <your-repo-url>
git push -u origin main
```

### 2. Deploy on Railway

1. Go to [railway.app](https://railway.app) and create a new project
2. Select **Deploy from GitHub repo**
3. Choose your repository
4. Go to **Variables** tab and add all environment variables from `.env`
5. Set `WEBHOOK_URL` to your Railway domain (e.g. `https://your-app.up.railway.app`)
6. Railway will auto-detect the `Procfile` and deploy

### 3. Verify

- Check the health endpoint: `GET https://your-app.up.railway.app/health`
- Expected response: `{"status": "ok", "bot": "running"}`
- Send `/start` to your bot on Telegram

---

## Project Structure

```
hostfi-bot/
├── main.py                    # FastAPI app + webhook
├── config.py                  # Environment configuration
├── requirements.txt           # Dependencies
├── .env.example               # Env template
├── railway.toml               # Railway config
├── Procfile                   # Process definition
│
├── bot/
│   ├── application.py         # Handler registration
│   ├── handlers/
│   │   ├── community.py       # Welcome, CAPTCHA, flood control
│   │   ├── moderation.py      # Warn, mute, ban, kick
│   │   ├── support.py         # AI /ask command
│   │   ├── market.py          # Price, rates, alerts
│   │   ├── broadcast.py       # Broadcast, poll, XP, referrals
│   │   ├── tickets.py         # Support tickets
│   │   └── admin.py           # Stats, lookup, reindex
│   ├── filters/
│   │   ├── spam_filter.py     # Spam detection
│   │   └── scam_filter.py     # Scam detection
│   └── utils/
│       ├── permissions.py     # Admin checks
│       ├── formatter.py       # HTML formatters
│       ├── keyboards.py       # Inline keyboards
│       └── rate_limiter.py    # Rate limiting
│
├── rag/
│   ├── ingestion.py           # Knowledge base ingestion
│   ├── retriever.py           # ChromaDB search
│   ├── ai_engine.py           # Groq API
│   ├── guardrails.py          # Safety checks
│   └── knowledge_base/        # .txt knowledge files
│
├── scheduler/
│   └── tasks.py               # APScheduler jobs
│
└── database/
    ├── client.py              # Supabase client
    ├── users.py               # User CRUD
    ├── tickets.py             # Ticket CRUD
    ├── referrals.py           # Referral CRUD
    ├── alerts.py              # Price alert CRUD
    ├── logs.py                # Audit logs
    └── schema.sql             # Database schema
```

---

## Scheduled Jobs

| Job | Schedule | Target |
|---|---|---|
| Daily Market Digest | 9:00 AM WAT | Community group |
| Daily Admin Report | 7:00 AM WAT | Admin channel |
| Weekly Leaderboard | Sunday 12:00 PM WAT | Community group |
| Price Alert Checker | Every 5 minutes | User DMs |
| Ticket Escalation | Every 30 minutes | Admin channel |

---

## Commands Reference

### User Commands
| Command | Description |
|---|---|
| `/start` | Start bot + referral link |
| `/help` | Show all commands |
| `/rules` | Community rules |
| `/price [coin]` | Live crypto price |
| `/rates` | Exchange rates |
| `/market` | Top 10 by market cap |
| `/fear` | Fear & Greed Index |
| `/alert set\|cancel\|list` | Price alerts |
| `/ask [question]` | AI assistant |
| `/support` | Open support ticket |
| `/rank` | Your XP rank |
| `/leaderboard` | Top 10 members |

### Admin Commands
| Command | Description |
|---|---|
| `/warn` | Warn a user (3 = auto-ban) |
| `/mute` | Mute a user |
| `/unmute` | Unmute a user |
| `/ban` | Ban a user |
| `/unban` | Unban a user |
| `/kick` | Kick a user |
| `/pin` | Pin a message |
| `/announce` | Send announcement |
| `/broadcast` | Broadcast to community |
| `/poll` | Create a poll |
| `/tickets` | View active tickets |
| `/reply` | Reply to ticket user |
| `/close` | Resolve a ticket |
| `/stats` | Bot statistics |
| `/lookup` | Look up a user |
| `/reindex` | Re-ingest knowledge base |
| `/adminhelp` | Admin command reference |

---

## License

Proprietary — HOSTFI. All rights reserved.
