# Vula Group — Deployment Guide

This guide covers everything needed to take the platform from a fresh Ubuntu 22.04 server (or the Vula Box / desktop) to fully live: AI models, vector store, API, dashboard, and all third-party integrations.

---

## Contents

1. [Hardware requirements](#1-hardware-requirements)
2. [Server setup](#2-server-setup)
3. [Ollama — local AI models](#3-ollama--local-ai-models)
4. [Qdrant — vector store](#4-qdrant--vector-store)
5. [vula_mind — Python API backend](#5-vula_mind--python-api-backend)
6. [vula_dashboard — React frontend](#6-vula_dashboard--react-frontend)
7. [nginx — reverse proxy + TLS](#7-nginx--reverse-proxy--tls)
8. [Third-party integrations](#8-third-party-integrations)
9. [Supabase database migrations](#9-supabase-database-migrations)
10. [Mobile app (vula_mobile)](#10-mobile-app-vula_mobile)
11. [Production checklist](#11-production-checklist)
12. [Monitoring & logs](#12-monitoring--logs)

---

## 1. Hardware requirements

| Component | Minimum | Recommended (Vula Box) |
|---|---|---|
| CPU | 8-core | 16-core |
| RAM | 16 GB | 32 GB |
| GPU VRAM | 8 GB (RTX 3070) | 24 GB (RTX 3090/4090) |
| SSD | 500 GB NVMe | 2 TB NVMe |
| Network | 100 Mbps LAN | Gigabit + WiFi 6 |

The `deepseek-r1:14b` model requires ~10 GB VRAM. With 24 GB you can run 14b + 8b simultaneously.

---

## 2. Server setup

```bash
# Ubuntu 22.04 — run as root or sudo
apt update && apt upgrade -y
apt install -y git python3.11 python3.11-venv python3-pip nginx certbot python3-certbot-nginx curl docker.io

# Create service user
useradd -m -s /bin/bash vula
usermod -aG docker vula

# Clone repo
su - vula
git clone https://github.com/vula-group/vula-group.git /home/vula/vula-group
```

---

## 3. Ollama — local AI models

```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh
systemctl enable ollama
systemctl start ollama

# Pull required models (do this in order — takes 30-60 min on first run)
ollama pull bge-m3              # 567 MB — embeddings (required)
ollama pull llama3.1:8b         # 4.7 GB — edge/fast tasks
ollama pull deepseek-r1:8b      # 4.9 GB — standard reasoning
ollama pull deepseek-r1:14b     # 9.0 GB — complex reasoning
ollama pull llava:7b            # 4.7 GB — OCR / image reading

# Verify all models are present
ollama list
```

Configure Ollama to listen on all interfaces (needed if API is on separate machine):

```bash
# /etc/systemd/system/ollama.service.d/override.conf
[Service]
Environment="OLLAMA_HOST=0.0.0.0"

systemctl daemon-reload && systemctl restart ollama
```

---

## 4. Qdrant — vector store

```bash
# Run Qdrant in Docker (persistent storage)
docker run -d \
  --name qdrant \
  --restart unless-stopped \
  -p 6333:6333 \
  -v /data/qdrant:/qdrant/storage \
  qdrant/qdrant

# Verify
curl http://localhost:6333/collections
```

For production with auth:

```bash
docker run -d --name qdrant --restart unless-stopped \
  -p 6333:6333 \
  -v /data/qdrant:/qdrant/storage \
  -e QDRANT__SERVICE__API_KEY=your-qdrant-api-key \
  qdrant/qdrant

# Then set in .env:
# QDRANT_API_KEY=your-qdrant-api-key
```

---

## 5. vula_mind — Python API backend

```bash
cd /home/vula/vula-group/vula_mind

# Create virtualenv
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
nano .env   # fill in all values — see section 8

# Test the configuration
python -m pytest tests/ -q

# Run (development)
uvicorn vula.api.server:app --host 0.0.0.0 --port 7438 --reload

# Run (production — use systemd)
```

### systemd service

```ini
# /etc/systemd/system/vula-api.service
[Unit]
Description=Vula AI API
After=network.target ollama.service

[Service]
User=vula
WorkingDirectory=/home/vula/vula-group/vula_mind
Environment="PATH=/home/vula/vula-group/vula_mind/venv/bin"
ExecStart=/home/vula/vula-group/vula_mind/venv/bin/uvicorn \
    vula.api.server:app \
    --host 127.0.0.1 \
    --port 7438 \
    --workers 2 \
    --log-level info
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable vula-api
systemctl start vula-api
systemctl status vula-api
```

---

## 6. vula_dashboard — React frontend

```bash
cd /home/vula/vula-group/vula_dashboard

# Install dependencies
npm install

# Configure
cat > .env.local << 'EOF'
VITE_API_URL=https://app.vula.ai
VITE_TEAM_WHATSAPP=+27820000000
EOF

# Build for production
npm run build

# Output is in dist/ — copy to nginx web root
cp -r dist /var/www/vula-dashboard/
```

For ongoing deployments:

```bash
# vula_dashboard/deploy.sh
#!/bin/bash
set -e
cd /home/vula/vula-group/vula_dashboard
git pull
npm install
npm run build
cp -r dist /var/www/vula-dashboard/
echo "Dashboard deployed"
```

---

## 7. nginx — reverse proxy + TLS

```bash
# Copy config
cp /home/vula/vula-group/nginx.conf /etc/nginx/sites-available/vula
ln -s /etc/nginx/sites-available/vula /etc/nginx/sites-enabled/vula
rm -f /etc/nginx/sites-enabled/default

# Test config
nginx -t

# Get TLS certificate (replace with your domain)
certbot --nginx -d app.vula.ai -d www.vula.ai --non-interactive --agree-tos -m ssl@vula.ai

# Start/reload
systemctl enable nginx
systemctl reload nginx
```

The `nginx.conf` in this repo is pre-configured with:
- HTTP → HTTPS redirect
- TLS 1.2/1.3 with strong cipher suites
- Rate limiting: 30 req/min API, 5 req/min onboarding endpoint
- Security headers (HSTS, X-Frame-Options, CSP)
- 120s proxy timeout for LLM responses
- Static dashboard at `/var/www/vula-dashboard/dist`

Update the `server_name` directive in `nginx.conf` to match your domain before deploying.

---

## 8. Third-party integrations

Fill in `vula_mind/.env` with each of these:

### Supabase (tenant database)

1. Create free project at [supabase.com](https://supabase.com)
2. Project Settings → API → copy `Project URL` → `SUPABASE_URL`
3. Project Settings → API → copy `service_role` key → `SUPABASE_SERVICE_KEY`
4. Run the migration SQL (see section 9)

### WhatsApp Business API (signup notifications)

1. [business.facebook.com](https://business.facebook.com) → WhatsApp → API Setup
2. Copy **Phone Number ID** → `WHATSAPP_PHONE_ID`
3. Create a permanent token → `WHATSAPP_TOKEN`
4. Set `TEAM_WHATSAPP` to Richard/Judy's number (receives new signup alerts)

### PayFast (subscription payments)

1. [payfast.co.za](https://payfast.co.za) → register merchant account
2. Integration → Merchant details → copy **Merchant ID** → `PAYFAST_MERCHANT_ID`
3. Copy **Merchant Key** → `PAYFAST_MERCHANT_KEY`
4. In PayFast settings, add ITN (webhook) URL: `https://app.vula.ai/api/v1/payfast/notify`
5. Set `DEBUG=false` in production — this switches PayFast from sandbox to live

### Resend (transactional email)

1. [resend.com](https://resend.com) → create free account
2. Domains → add `vula.ai` → add DNS records (SPF, DKIM) to your DNS provider
3. API Keys → create key → `RESEND_API_KEY`
4. Set `FROM_EMAIL=Vula Group <hello@vula.ai>` (must match verified domain)
5. Set `TEAM_EMAIL=team@vula.ai` (receives new signup HTML alert)

---

## 9. Supabase database migrations

The canonical migrations live in `vula_mind/migrations/`. Run them in order in your Supabase project's SQL Editor (Dashboard → SQL Editor → New query → paste the file contents):

| Order | File | Purpose |
|---|---|---|
| 1 | `vula_mind/migrations/001_vula_tenants.sql` | Core tenants + documents tables, RLS, admin view |
| 2 | `vula_mind/migrations/002_vula_commerce.sql` | Vula Commerce products/carts/orders (run if you're onboarding a commerce tenant; safe to skip otherwise) |

To verify after running:

```sql
SELECT table_name FROM information_schema.tables
 WHERE table_schema = 'public' AND table_name LIKE 'vula_%' OR table_name LIKE 'commerce_%';
```

You should see `vula_tenants`, `vula_documents`, `commerce_products`, `commerce_carts`, `commerce_orders`, etc.

If you're upgrading from an earlier deploy that predates the `paid` column on `vula_tenants`, re-running `001_vula_tenants.sql` is idempotent — it includes an `ALTER TABLE … ADD COLUMN IF NOT EXISTS paid` to fill in the gap.

---

## 10. Mobile app (vula_mobile)

```bash
cd /home/vula/vula-group/vula_mobile

# Install dependencies
npm install

# iOS (Mac only)
npx expo run:ios

# Android
npx expo run:android

# Or run in Expo Go (development)
npx expo start
```

The mobile app connects to the API via the Settings screen. On first launch, set:
- **Backend host**: your server IP or domain, e.g. `http://192.168.1.100:7438` (LAN) or `https://app.vula.ai` (production)
- **API Key**: must match `API_KEY` set in `vula_mind/.env`

---

## 11. Production checklist

Before going live, verify each item:

**Security**
- [ ] `API_KEY` is set to a strong random value (32+ hex chars)
- [ ] `DEBUG=false` in `.env`
- [ ] Swagger UI is disabled (automatic when `DEBUG=false`)
- [ ] TLS certificate is valid and auto-renewing (`certbot renew --dry-run`)
- [ ] nginx security headers are active (`curl -I https://app.vula.ai`)
- [ ] Qdrant is not exposed to the internet (127.0.0.1 only, or firewall rule)
- [ ] Ollama is not exposed to the internet (127.0.0.1 only)

**Integrations**
- [ ] Supabase migration has been run — test by POSTing to `/v1/onboard`
- [ ] WhatsApp sends to `TEAM_WHATSAPP` on test signup
- [ ] Email arrives at `TEAM_EMAIL` on test signup
- [ ] Client welcome email arrives with correct workspace URL
- [ ] PayFast sandbox payment completes and `/v1/payfast/notify` fires
- [ ] Switch `DEBUG=false` to enable live PayFast

**AI models**
- [ ] `ollama list` shows all 5 models (bge-m3, llama3.1:8b, deepseek-r1:8b, deepseek-r1:14b, llava:7b)
- [ ] `/status` endpoint returns `"status": "ok"` for both Ollama and Qdrant
- [ ] Test document ingestion: `POST /ingest` with a PDF
- [ ] Test query: `POST /query` returns answer with sources

**Dashboard**
- [ ] Production build loads without console errors
- [ ] Query interface sends to correct API URL
- [ ] Admin signups panel loads (requires `API_KEY` header)

---

## 12. Monitoring & logs

```bash
# API logs (live)
journalctl -u vula-api -f

# nginx access + error logs
tail -f /var/log/nginx/access.log
tail -f /var/log/nginx/error.log

# Ollama logs
journalctl -u ollama -f

# Check API health
curl https://app.vula.ai/status

# Check Qdrant collections
curl http://localhost:6333/collections
```

### Log format

The API logs every request with a correlation ID stamped in `X-Request-ID`. To trace a specific request across logs:

```bash
journalctl -u vula-api | grep "req_id_here"
```

### Disk space

Uploads accumulate in `UPLOAD_DIR` (default: `/tmp/vula_uploads`). In production, set this to a persistent path and monitor usage:

```bash
# In .env
UPLOAD_DIR=/data/vula/uploads
TAKEOFF_UPLOAD_DIR=/data/vula/takeoff
```

Set up a cron job to clean uploads older than 30 days:

```bash
# /etc/cron.daily/vula-cleanup
#!/bin/bash
find /data/vula/uploads -mtime +30 -type f -delete
find /data/vula/takeoff -mtime +30 -type f -delete
```
