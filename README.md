# Vula — Unlock Your Business

> **AI that runs in your world, not theirs.**

Every AI assistant processes your data in someone else's cloud, on someone else's hardware, under someone else's terms. **Vula runs on your devices, in your building, on your mesh.** POPIA-compliant. Zero cloud cost at runtime. Built in Cape Town.

---

## Repo Structure

```
vula_mind/          Python AI backend — HRM + ThinKMesh + memory + API
vula_dashboard/     React web dashboard — ask questions, upload docs, web intel
vula_mobile/        React Native / Expo mobile thin client
infrastructure/     Shared infra configs (LiteLLM gateway)
n8n_workflows/      Workflow automation (daily briefings, follow-ups)
```

### Tenants

Vula is the backend. Anything that consumes the API is a tenant. Each tenant gets its own row in `vula_tenants`, its own Qdrant collection, and (for commerce tenants) its own products/carts/orders in the `commerce_*` tables.

Prospective and example tenants that have scaffolding in this repo:

| Tenant id | Status | What lives in this repo |
|---|---|---|
| `digg-demo` | seeded demo | tenant row only |
| `off-the-hook` | prospective | `migrations/002_vula_commerce.sql`, `scripts/seed_off_the_hook.py`, `n8n_workflows/off_the_hook_workflows.json`, WhatsApp commerce flow in `vula/api/whatsapp.py` |
| `awake-sa` | prospective | covered by the same commerce migration |

Storefront UIs (e.g. a Next.js shop for Off the Hook) are not vendored here — they would be built and deployed separately and call this backend over HTTPS.

---

## Architecture

```
Mobile (vula_mobile / TwoSoul)
        ↓ HTTP on local WiFi
vula_mind API  (FastAPI · port 7438)
        ↓
HRM Orchestrator  →  complexity score + skill assignment
        ↓
ThinKMesh Executor  →  parallel DeepSeek R1 branches (Ollama)
        ↓
Merger  →  synthesise or pick best branch output
        ↓
Reflection Agent  →  score outcome, write SQLite memory, improve routing
```

**Document ingestion pipeline:**
```
Upload → OCR (GLM-OCR / pdfplumber) → Chunker → BGE-M3 embeddings → Qdrant → DeepSeek RAG
```

---

## Quick Start

```bash
# 1. Start models
ollama pull deepseek-r1:7b && ollama pull bge-m3

# 2. Start vector store
docker run -p 6333:6333 qdrant/qdrant

# 3. Start backend
cd vula_mind && pip install -r requirements.txt
uvicorn vula.api.server:app --host 0.0.0.0 --port 7438 --reload

# 4. Start dashboard
cd vula_dashboard && npm install && npm run dev
# → http://localhost:3000

# 5. CLI mode
cd vula_mind && python main.py
```

---

## Model Tiers

| Tier | Model | Use |
|------|-------|-----|
| Edge | `deepseek-r1:1.5b` | Routing, quick answers |
| Worker | `deepseek-r1:7b` | 80% of daily tasks |
| Reasoner | `deepseek-r1:14b` | Complex analysis, BOQ, financial |

---

## Products Built on This Stack

| Product | Description |
|---------|-------------|
| **Vula Dashboard** | Ask questions, upload docs, web intel — runs on desktop |
| **Vula QS** | Instant construction cost estimator (AECOM 2025/26 rates) |
| **Vula Takeoff** | AI reads architectural plans → auto-generates BOQ |
| **Vula Mobile** | Expo thin client — dispatches to the desktop mesh |

---

## What Makes This Different

| Feature | Cloud AI (ChatGPT, Claude, Gemini) | Vula |
|---|---|---|
| Data privacy | Your data on their servers | 100% local, zero telemetry |
| Cost at scale | Per-token API fees forever | Hardware cost once |
| Offline use | Requires internet | Works air-gapped |
| Multi-device | Single cloud endpoint | Mesh across all your devices |
| Self-improving | Static model | Learns from outcomes locally |
| Open source | Closed / proprietary | MIT — fork it, own it |

---

## Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for the full production guide: hardware, Ollama, Qdrant, the FastAPI backend, the React dashboard, nginx + TLS, Supabase migrations, and third-party integrations (WhatsApp, PayFast, Resend).

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the skill-authoring guide.

---

## License

MIT — see [LICENSE](LICENSE)

---

## Built By

**Vula Group (Pty) Ltd** — Cape Town, South Africa
GitHub: [@Awehbelekker](https://github.com/Awehbelekker/Vula-Group)
