# Vula — Unlock Your Business

> **AI that runs in your world, not theirs.**

Every AI assistant processes your data in someone else's cloud, on someone else's hardware, under someone else's terms. **Vula runs on your devices, in your building, on your mesh.** POPIA-compliant. Zero cloud cost at runtime. Built in Cape Town.

---

## Repo Structure

```
vula_mind/          Python AI backend — HRM + ThinKMesh + memory + API
vula_dashboard/     React web dashboard — ask questions, upload docs, web intel
vula_mobile/        React Native / Expo mobile thin client
docs/               Architecture docs and integration guides
```

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

## License

MIT — built by Aweh Be Lekker (Pty) Ltd, Cape Town.

Built in Cape Town. Built for everyone.

---

## What Makes This Different

| Feature | Cloud AI (ChatGPT, Claude, Gemini) | Universal Soul |
|---|---|---|
| Data privacy | Your data on their servers | 100% local, zero telemetry |
| Cost at scale | Per-token API fees forever | Hardware cost once |
| Offline use | Requires internet | Works air-gapped |
| Multi-device | Single cloud endpoint | Mesh across all your devices |
| Self-improving | Static model | Learns from outcomes locally |
| Open source | Closed / proprietary | MIT — fork it, own it |

---

## Core Architecture

```
Mobile (Thin Client — TwoSoul)
        ↓  task dispatch (encrypted)
HRM Orchestrator (27M — decision engine)
        ↓  complexity score + skill assignment
ThinKMesh (parallel branch execution)
   Branch A          Branch B          Branch C
   DeepSeek 1.5B     DeepSeek 7B       DeepSeek 14B
   (triage/routing)  (standard tasks)  (hard reasoning)
        ↓  merge + synthesize
Skill Registry (dynamic tool loading)
        ↓
Memory Store (SQLite local + Qdrant semantic)
        ↓
Reflection Agent (post-task scoring → learning delta)
```

### Key Components

**HRM (Hierarchical Reasoning Model — 27M params)**
Your orchestration brain. Lightweight enough to run on any device. Does not generate answers — it decides which model on which device handles each subtask. H-module plans, L-module executes routing decisions.

**ThinKMesh**
Parallel reasoning across your device mesh. Unlike sequential chain-of-thought, ThinKMesh spawns multiple reasoning branches simultaneously and merges them. Intercepts DeepSeek `<think>` traces from each branch as merge signal — not just final answers.

**Skill Registry**
Every capability is a file in `/core/skills/`. HRM matches tasks to skills via the registry manifest. Adding a new capability is dropping a file — not a platform update. Community-contributed skills are the growth engine.

**Mesh Transport**
Device discovery via mDNS. X25519 key exchange on first connect. AES-256-GCM encrypted payload. WhatsApp Business API fallback when mesh is unavailable.

---

## Supported Models (Local, Open Source)

| Model | Size (Q4_K_M) | VRAM | Role |
|---|---|---|---|
| DeepSeek R1 1.5B | ~1.1GB | 2GB | Routing, triage, mobile edge |
| DeepSeek R1 7B | ~4.5GB | 6GB | Standard task execution |
| DeepSeek R1 14B | ~8.5GB | 10GB | Complex reasoning, code |
| DeepSeek R1 32B | ~19GB | 22GB | RTX 3090 ceiling tier |

All models run via **Ollama** (GPU) or **llama.cpp** (CPU fallback). No API keys. No subscriptions.

---

## Quick Start

### Requirements
- Python 3.11+
- Ollama installed ([ollama.ai](https://ollama.ai))
- 8GB+ RAM (16GB recommended)
- NVIDIA GPU optional but recommended

### Install

```bash
git clone https://github.com/Awehbelekker/Universal-AI-Soul-Unlimited.git
cd Universal-AI-Soul-Unlimited
pip install -r requirements.txt
```

### Pull your first model

```bash
ollama pull deepseek-r1:7b
```

### Run

```bash
python main.py
```

### Android APK

```bash
pip install buildozer
buildozer -v android debug
```

---

## Project Structure

```
Universal-AI-Soul-Unlimited/
├── LICENSE
├── README.md
├── requirements.txt
├── main.py                        # Single entry point
│
├── core/
│   ├── hrm/                       # HRM 27M orchestration engine
│   │   ├── __init__.py
│   │   ├── orchestrator.py        # Main HRM router
│   │   ├── h_module.py            # High-level planner
│   │   └── l_module.py            # Low-level executor
│   │
│   ├── thinkmesh/                 # Parallel reasoning engine
│   │   ├── __init__.py
│   │   ├── graph.py               # TaskGraph + TaskBranch dataclasses
│   │   ├── executor.py            # Parallel branch runner
│   │   └── merger.py              # Branch merge strategies
│   │
│   ├── memory/                    # Persistent memory
│   │   ├── __init__.py
│   │   ├── local.py               # SQLite session memory
│   │   ├── semantic.py            # Qdrant long-term memory
│   │   └── reflection.py         # Post-task learning delta
│   │
│   └── skills/                    # Skill registry + implementations
│       ├── registry.json
│       ├── web_search.py
│       ├── code_execution.py
│       ├── memory_recall.py
│       ├── file_parse.py
│       └── reasoning.py
│
├── mesh/                          # Device mesh networking
│   ├── __init__.py
│   ├── discovery.py               # mDNS device discovery
│   ├── transport.py               # Encrypted WebSocket transport
│   └── fallback.py                # WhatsApp Business API fallback
│
├── mobile/                        # Android thin client (Kivy)
│   ├── main_android.py
│   ├── ui/
│   └── buildozer.spec
│
├── models/
│   ├── registry.json              # Model tier definitions
│   ├── Modelfile                  # GPU Ollama config
│   └── Modelfile.cpu              # CPU fallback config
│
├── benchmarks/
│   ├── benchmark_thinkmesh.py
│   └── benchmark_hrm.py
│
└── tests/
    ├── test_hrm.py
    ├── test_thinkmesh.py
    └── test_mesh.py
```

---

## Contributing Skills

The skill registry is the community growth engine. To add a skill:

1. Create `core/skills/your_skill_name.py`
2. Implement the `BaseSkill` interface
3. Add an entry to `core/skills/registry.json`
4. Submit a pull request

See [CONTRIBUTING.md](docs/CONTRIBUTING.md) for the full guide.

---

## Roadmap

- [x] HRM 27M orchestration engine
- [x] ThinKMesh parallel reasoning
- [x] DeepSeek R1 model tier stack
- [x] Android thin client (Kivy/Buildozer)
- [x] Skill registry pattern
- [ ] Qdrant semantic memory integration
- [ ] Mesh device discovery (mDNS)
- [ ] Encrypted WebSocket transport
- [ ] Reflection learning loop
- [ ] WhatsApp fallback delivery
- [ ] iOS thin client
- [ ] DeepSeek V4-Flash support (when GGUF drops)
- [ ] Web dashboard

---

## License

MIT — see [LICENSE](LICENSE)

---

## Built By

**Aweh Be Lekker (Pty) Ltd** — Cape Town, South Africa
GitHub: [@Awehbelekker](https://github.com/Awehbelekker)
