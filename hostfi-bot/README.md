# HOSTFI Telegram Bot

Community management, AI-powered support, and crypto market data bot for HOSTFI.

---

## Features

- **Community Management** — Welcome flow with math CAPTCHA, spam/scam detection, flood control
- **AI Support** — RAG-powered Q&A using HOSTFI knowledge base (Gemini + ChromaDB)
- **Live Market Data** — Crypto prices, market overview, Fear & Greed Index, price alerts
- **Broadcast & Engagement** — Admin broadcasts, native polls, cycle-based XP campaign, invite tracking, X raids
- **Support Tickets** — Full ticket lifecycle with claim, reply, close, and rating
- **Admin Dashboard** — Stats, user lookup, knowledge base re-indexing, daily reports

---

## Tech Stack

| Component | Technology |
|---|---|
| Bot Framework | python-telegram-bot 20.7 (async, webhook) |
| Web Server | FastAPI + uvicorn |
| AI | Gemini API (`gemini-2.5-flash`) |
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
- Gemini API key (from [Google AI Studio](https://aistudio.google.com/))
- Upstash Redis instance (see Redis Setup below)

> **ChromaDB** and **CoinGecko** require **zero setup** — ChromaDB runs locally (auto-creates a `chroma_db/` folder on first run) and CoinGecko's free API needs no API key.

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

Edit `.env` with your actual credentials. **Detailed instructions for each variable are in the sections below.**

| Variable | Required? | Description | Where to get it |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | **Yes** | Bot token from BotFather | See **Bot Token Setup** |
| `TELEGRAM_WEBHOOK_SECRET` | **Yes** | Random secret for webhook security | See **Webhook Secret** |
| `WEBHOOK_URL` | **Yes** | Your public URL | See **Run Locally** or **Railway Deployment** |
| `SUPERADMIN_ID` | **Yes** | Your Telegram user ID (bot owner) | See **Getting Telegram IDs** |
| `COMMUNITY_GROUP_ID` | **Yes** | Community group ID (starts with `-100`) | See **Getting Telegram IDs** |
| `GEMINI_API_KEY` | **Yes** | Gemini API key | See **Gemini Setup** |
| `GEMINI_MODEL` | No | Gemini model, defaults to `gemini-2.5-flash` | `.env.example` |
| `X_BEARER_TOKEN` | For XP campaign X features | Official X API bearer token | X Developer Portal |
| `X_API_BASE_URL` | No | X API base URL, defaults to `https://api.x.com/2` | `.env.example` |
| `SUPABASE_URL` | **Yes** | Supabase project URL | See **Supabase Setup** |
| `SUPABASE_KEY` | **Yes** | Supabase anon/public key | See **Supabase Setup** |
| `ADMIN_IDS` | No | Extra admin IDs (optional — see below) | See **How Admins Work** |
| `ADMIN_CHANNEL_ID` | No | Private admin channel ID | See **Getting Telegram IDs** |
| `UPSTASH_REDIS_URL` | No | Upstash Redis URL | See **Redis Setup** |
| `UPSTASH_REDIS_TOKEN` | No | Upstash Redis token | See **Redis Setup** |

> **Minimum to get running:** You only need the 8 "Required" variables. The bot will work without Redis (rate limiting is skipped), without ADMIN_CHANNEL_ID (admin alerts are skipped), and without ADMIN_IDS (admins are auto-detected from the group).

### 4. Supabase Setup

1. Create a new Supabase project at [supabase.com](https://supabase.com)
2. Go to **SQL Editor** and run the full schema from `database/schema.sql`
3. To find your credentials: go to **Settings → API**
   - `SUPABASE_URL` = the **Project URL** (looks like `https://xxxx.supabase.co`)
   - `SUPABASE_KEY` = the **anon public** key (the long `eyJ...` string under "Project API keys")
4. Paste both into your `.env`

### 5. Gemini Setup

1. Open [Google AI Studio](https://aistudio.google.com/)
2. Go to **API Keys**
3. Click **Create API Key**, give it a name, and copy the key
4. Add it to `.env` as `GEMINI_API_KEY`
5. Keep `GEMINI_MODEL=gemini-2.5-flash` unless you intentionally change models

### 6. Redis Setup

The bot uses Redis for rate limiting and caching. You have two options:

**Option A — Upstash (recommended for Railway deployment):**

1. Go to [console.upstash.com](https://console.upstash.com) and sign up (Google/GitHub login works)
2. Click **Create Database**
3. Pick a name (e.g. `hostfi-bot`), choose the region closest to your Railway server, and click **Create**
4. On the database details page, find:
   - `UPSTASH_REDIS_URL` = the **REST URL** (starts with `https://...upstash.io`)
   - `UPSTASH_REDIS_TOKEN` = the **REST Token** (long string below the URL)
5. Paste both into `.env`

> If upstash.com gives you errors, try opening it in a different browser or use incognito mode. Their console works best in Chrome.

**Option B — Railway Redis (if Upstash is unavailable):**

1. In your Railway project dashboard, click **+ New** → **Database** → **Redis**
2. Railway will spin up a Redis instance. Click on it and go to **Connect**
3. Use the **Public URL** as `UPSTASH_REDIS_URL`
4. Set `UPSTASH_REDIS_TOKEN` to any placeholder value (e.g. `railway`) — the code's rate limiter will still work as it falls open on auth failure

> The bot works fine even if Redis is temporarily unavailable — rate limiting and caching just get skipped.

### 7. Bot Token Setup

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts (pick a name and username)
3. BotFather will give you a token like `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`
4. Copy it to `.env` as `TELEGRAM_BOT_TOKEN`

### 8. Webhook Secret

This is just a random password that ensures only Telegram can send updates to your bot:

1. Generate a random string — you can use Python:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
2. Copy the output and paste it into `.env` as `TELEGRAM_WEBHOOK_SECRET`

### 9. How Admins Work (Like Rose Bot)

The bot works **exactly like Rose** — you do NOT need to manually list admin IDs.

**Anyone you promote to admin in your Telegram group automatically has admin access to the bot.** The bot checks Telegram's group admin list in real-time. If you promote someone to admin in the group, they can immediately use `/warn`, `/ban`, `/broadcast`, etc. If you demote them, they lose access.

**SUPERADMIN_ID** is the bot owner (you). It gives you one extra power that regular admins don't have:
- `/reindex` — rebuild the AI knowledge base (this touches the bot's core data, so only the owner should do it)
- The superadmin can never be warned/muted/banned by other admins

**ADMIN_IDS** (optional) is for adding extra people who should be admins *even if they're not group admins* — for example, a developer or support person who doesn't need to be in the group. Leave it empty if you don't need this.

### 10. Getting Telegram IDs

**Your User ID (for SUPERADMIN_ID):**
1. Message [@userinfobot](https://t.me/userinfobot) on Telegram
2. It will reply with your user ID (a number like `6129358034`)
3. Set `SUPERADMIN_ID` to this number

**Community Group ID (for COMMUNITY_GROUP_ID):**
1. Add [@RawDataBot](https://t.me/RawDataBot) to your community group temporarily
2. Send any message in the group — RawDataBot will reply with JSON data
3. Look for `"chat": {"id": -100xxxxxxxxxx}` — that negative number is your group ID
4. Set `COMMUNITY_GROUP_ID` to that number (e.g. `-1001234567890`)
5. Remove @RawDataBot from the group — you only need it once

**Admin Channel ID (for ADMIN_CHANNEL_ID — optional):**

The admin channel is a **private Telegram channel** where the bot sends alerts (spam detections, ticket escalations, daily reports). It's optional — the bot works without it, you just won't get admin alerts.

To set it up:
1. Create a new **private channel** in Telegram (e.g. "HOSTFI Admin Alerts")
2. Add your bot as an admin of the channel (go to channel settings → Administrators → Add Admin → search for your bot)
3. Add @RawDataBot to the channel temporarily, send a message, and grab the channel ID the same way
4. Set `ADMIN_CHANNEL_ID` to that number
5. Remove @RawDataBot

### 11. Setting Bot Commands in BotFather (User vs Admin)

When users type `/` in the chat, Telegram shows a command menu. **Only register user-facing commands** — admin commands stay hidden.

1. Message [@BotFather](https://t.me/BotFather)
2. Send `/setcommands`
3. Select your bot
4. Paste these **user commands only**:
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
   campaign - Current XP campaign
   xp - Your campaign XP
   invite - Get your campaign invite link
   xlink - Link your X account
   xverify - Verify your X account
   raids - Active HostFi raids
   xpost - Submit a HostFi X post
   rank - Your XP and rank
   leaderboard - Top 10 members
   ```

**Admin commands** (`/warn`, `/mute`, `/ban`, `/kick`, `/broadcast`, `/stats`, etc.) are NOT registered in BotFather — they still work, they just don't show up in the `/` menu for regular users. The bot auto-detects group admins and only lets them use these commands.

Any admin can type `/adminhelp` to see the full list of admin commands.

### 12. ChromaDB (No Setup Needed)

ChromaDB is the vector database that powers the AI knowledge base. It requires **zero setup** — no account, no server, no API key.

**When does it get created?** When you send the `/reindex` command to the bot in Telegram. The bot reads the knowledge base text files, converts them into AI embeddings, and stores them in a `chroma_db/` folder inside the project directory. This only needs to be done once — then the AI assistant can answer questions.

- If you delete the `chroma_db/` folder, just send `/reindex` again to rebuild it
- On Railway, the folder persists as long as your deployment volume exists

### 12. CoinGecko (No Setup Needed)

The bot uses CoinGecko's **free public API** for crypto prices, market data, and exchange rates. No API key is needed.

- Free tier: ~30 requests/minute (more than enough for the bot)
- The bot caches responses in Redis for 60 seconds to avoid hitting limits
- Supports BTC, ETH, BNB, SOL, XRP, DOGE, ADA, DOT, MATIC, AVAX, LINK, UNI, LTC

### 14. Quick Setup Order (What to Do First)

You don't need everything set up to start testing. Here's the recommended order:

1. **Get your bot token** from BotFather and set `TELEGRAM_BOT_TOKEN`
2. **Get your user ID** from @userinfobot and set `SUPERADMIN_ID`
3. **Create Supabase project**, run `schema.sql`, set `SUPABASE_URL` + `SUPABASE_KEY`
4. **Get Gemini API key** and set `GEMINI_API_KEY`
5. **Generate webhook secret** and set `TELEGRAM_WEBHOOK_SECRET`
6. **Set up ngrok** → set `WEBHOOK_URL` to the ngrok URL
7. **Run `python main.py`** — the bot will start! You can test `/start`, `/help`, `/price btc`, `/ask` in DM
8. **Create your community group** → add bot → get group ID with @RawDataBot → set `COMMUNITY_GROUP_ID`
9. **Optionally** create admin channel, set up Redis, etc.

> You can test most bot features (prices, AI, commands) without COMMUNITY_GROUP_ID or ADMIN_CHANNEL_ID. Those are only needed for group-specific features like welcome CAPTCHA and admin alerts. Set `COMMUNITY_GROUP_ID=0` and `ADMIN_CHANNEL_ID=0` temporarily.

### 15. Run Locally (Optional — Skip If Using Railway)

**You do NOT need to run locally.** If you're deploying on Railway, skip this entire section — just push your code to GitHub, set env vars in Railway's dashboard, and test directly on Telegram. Railway gives you a permanent public URL.

This section is only for developers who want to test changes on their own machine before pushing to Railway.

<details>
<summary>Click to expand local development instructions</summary>

**Why ngrok?** Telegram bots need a public URL — when someone sends `/start`, Telegram's servers forward the message to your server. Your laptop has no public IP, so ngrok creates a temporary tunnel.

```
User sends /start → Telegram servers → ngrok URL → your laptop → bot processes it → reply
```

**Step 1 — Install ngrok (free):**
1. Go to [ngrok.com](https://ngrok.com) and create a free account
2. Download ngrok for Windows, unzip it
3. Run: `ngrok config add-authtoken YOUR_TOKEN`

**Step 2 — Start the tunnel:**
```bash
ngrok http 8000
```
ngrok shows a public URL like `https://a1b2c3d4.ngrok-free.app`

**Step 3 — Update .env:**
```
WEBHOOK_URL=https://a1b2c3d4.ngrok-free.app
```

**Step 4 — Run the bot:**
```bash
cd hostfi-bot
.venv\Scripts\activate
python main.py
```

**Step 5 — Verify:**
- Open `https://a1b2c3d4.ngrok-free.app/health` in browser — should show `{"status": "ok", "bot": "running"}`
- Send `/start` to your bot on Telegram

> Every ngrok restart gives a new URL. On Railway, the URL is permanent — no need for any of this.

</details>

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
4. Go to **Variables** tab and add **at minimum** these env vars:
   ```
   TELEGRAM_BOT_TOKEN=your_token
   TELEGRAM_WEBHOOK_SECRET=your_secret
   WEBHOOK_URL=https://your-domain.up.railway.app    (set after step 5)
   SUPERADMIN_ID=your_user_id
   COMMUNITY_GROUP_ID=your_group_id                  (use 0 temporarily if you don't have it yet)
   ADMIN_CHANNEL_ID=0                                (set later when you create the admin channel)
   GEMINI_API_KEY=your_gemini_key
   GEMINI_MODEL=gemini-2.5-flash
   SUPABASE_URL=your_supabase_url
   SUPABASE_KEY=your_supabase_key
   ```
   Optional (add later):
   ```
   ADMIN_IDS=                    (leave empty — group admins are auto-detected)
   UPSTASH_REDIS_URL=            (leave empty if you don't have Redis yet)
   UPSTASH_REDIS_TOKEN=          (leave empty if you don't have Redis yet)
   X_BEARER_TOKEN=               (required only for X account linking, raids, and /xpost)
   X_API_BASE_URL=https://api.x.com/2
   ```
5. Go to **Settings → Networking → Public Networking** and click **Generate Domain**
6. Railway gives you a URL like `https://hostfi-bot-production.up.railway.app`
7. Go back to **Variables** and set `WEBHOOK_URL` to this Railway domain
8. Railway will auto-detect the `Procfile` and deploy

> **If Railway shows a build error:** It means you're missing required env vars. The bot needs `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `WEBHOOK_URL`, `SUPERADMIN_ID`, `GEMINI_API_KEY`, `SUPABASE_URL`, and `SUPABASE_KEY` to start. Set them all in the Variables tab and redeploy.

### 3. Verify

- Check the health endpoint: `GET https://your-railway-domain.up.railway.app/health`
- Expected response: `{"status": "ok", "bot": "running"}`
- Send `/start` to your bot on Telegram
- Send `/reindex` to initialize the AI knowledge base (first time only)

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
│   │   ├── broadcast.py       # Broadcast, poll, campaign leaderboard
│   │   ├── campaign.py        # XP cycles, invites, raids, X posts
│   │   ├── tickets.py         # Support tickets
│   │   └── admin.py           # Stats, lookup, reindex
│   ├── filters/
│   │   ├── spam_filter.py     # Spam detection
│   │   └── scam_filter.py     # Scam detection
│   └── utils/
│       ├── permissions.py     # Admin checks
│       ├── formatter.py       # HTML formatters
│       ├── keyboards.py       # Inline keyboards
│       ├── rate_limiter.py    # Rate limiting
│       └── x_api.py           # Official X API verification
│
├── rag/
│   ├── ingestion.py           # Knowledge base ingestion
│   ├── retriever.py           # ChromaDB search
│   ├── ai_engine.py           # Gemini API
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
    ├── referrals.py           # Legacy referral CRUD
    ├── campaign.py            # Campaign XP ledger CRUD
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
| Campaign Invite Award Checker | Every 30 minutes | Inviter DMs |
| Ticket Escalation | Every 30 minutes | Admin channel |

---

## Commands Reference

### User Commands
| Command | Description |
|---|---|
| `/start` | Start bot |
| `/help` | Show all commands |
| `/rules` | Community rules |
| `/price [coin]` | Live crypto price |
| `/rates` | Exchange rates |
| `/market` | Top 10 by market cap |
| `/fear` | Fear & Greed Index |
| `/alert set\|cancel\|list` | Price alerts |
| `/ask [question]` | AI assistant |
| `/support` | Open support ticket |
| `/campaign` | Current XP campaign rules and status |
| `/xp` | Your current campaign XP |
| `/invite` | Generate your campaign invite link |
| `/xlink @handle` | Start X account verification |
| `/xverify [url]` | Verify your X account |
| `/raids` | View active X raids |
| `/raid submit [id] [url]` | Submit raid proof |
| `/xpost [url]` | Submit a HostFi X post for XP |
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
| `/cycle start\|finish` | Start or finish campaign cycle |
| `/raid create [url] [hours]` | Create approved X raid |
| `/award helpful [reason]` | Award helpful contribution XP |
| `/xp add\|deduct\|disqualify` | Superadmin XP controls |
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
