# CLAUDE.md — MemoryManager (Engram Architecture)

> Brain-Inspired Memory Architecture for AI Agents
> Built on Hindsight (Vectorize) as foundation

---

## Project Overview

MemoryManager extends the open-source Hindsight memory system with a brain-inspired Engram architecture. The goal is to replace the flat fact-based memory model with a neuroscience-grounded system featuring:

- **Engrams** as central knowledge units (replacing memory_units)
- **Thalamus Filter** for relevance-gated ingestion
- **Session Modes** (Precision/Exploration/Analogy/Validation) controlling all operations
- **Constructive Memory** — retrieval as reconstruction, not lookup
- **Nightly Consolidation Run (NCR)** — 3-phase knowledge evolution
- **Schema Emergence** — abstract patterns emerge from repeated Engrams
- **Multi-Bank Architecture** — Agent Session → Agent Dictionary → Shared Memory

### Guiding Principle

> "What would the brain do?"

Every design decision maps to a neuroscience mechanism. See `docs/engram/concept.md` for the full architecture document with all 18 chapters (architecture + neuroscience mappings).

---

## Key References

| Document | Purpose |
|----------|---------|
| `docs/engram/concept.md` | Full architecture concept (18 chapters, bio→tech mappings) |
| `docs/engram/backlog/epic-overview.md` | All 15 Epics with dependencies and milestones |
| `docs/engram/backlog/milestones.md` | 6 Milestones with acceptance criteria and validation tests |
| `docs/engram/backlog/epic-NN-*/epic.md` | Individual Epic scope and stories |
| `docs/engram/backlog/epic-NN-*/story-NN-*.md` | Stories with embedded tasks as checklists |
| `AGENTS.md` | Architecture decisions and coding conventions |

---

## Development Commands

### API Server (Python/FastAPI)
```bash
# Start API server (loads .env automatically)
./scripts/dev/start-api.sh

# Run all tests (parallelized with pytest-xdist)
cd hindsight-api && uv run pytest tests/

# Run specific test file
cd hindsight-api && uv run pytest tests/test_http_api_integration.py -v

# Run single test function
cd hindsight-api && uv run pytest tests/test_retain.py::test_retain_simple -v

# Lint and format
cd hindsight-api && uv run ruff check .
cd hindsight-api && uv run ruff format .

# Type checking
cd hindsight-api && uv run ty check hindsight_api/
```

### Control Plane (Next.js)
```bash
./scripts/dev/start-control-plane.sh
```

### Documentation Site (Docusaurus)
```bash
./scripts/dev/start-docs.sh
```

### Generating Clients/OpenAPI
```bash
# Regenerate OpenAPI spec after API changes (REQUIRED after changing endpoints)
./scripts/generate-openapi.sh

# Regenerate all client SDKs (Python, TypeScript, Rust)
./scripts/generate-clients.sh
```

---

## Architecture

### Monorepo Structure
- **hindsight-api/**: Core FastAPI server with memory engine (Python, uv)
- **hindsight/**: Embedded Python bundle (hindsight-all package)
- **hindsight-control-plane/**: Admin UI (Next.js, npm)
- **hindsight-cli/**: CLI tool (Rust, cargo)
- **hindsight-clients/**: Generated SDK clients (Python, TypeScript, Rust)
- **hindsight-docs/**: Docusaurus documentation site
- **hindsight-integrations/**: Framework integrations (LiteLLM, OpenAI)
- **hindsight-dev/**: Development tools and benchmarks
- **docs/engram/**: Engram architecture concept + backlog (NEW)

### Core Engine (hindsight-api/hindsight_api/engine/)
- `memory_engine.py`: Main orchestrator for retain/recall/reflect operations
- `llm_wrapper.py`: LLM abstraction (OpenAI, Anthropic, Gemini, Groq, Ollama, LM Studio)
- `embeddings.py`: Embedding generation (local sentence-transformers or TEI)
- `cross_encoder.py`: Reranking (local or TEI)
- `entity_resolver.py`: Entity extraction and normalization
- `query_analyzer.py`: Query intent analysis

### Target Architecture (Engram)
- **PostgreSQL**: Agent Session Bank (session state, metadata, configuration)
- **Qdrant**: Content Store (Engram embeddings, vector search, tags)
- **Neo4j**: Graph Store (entities, relationships, 8 link types, schema nodes)
- **Engram ID Linking**: Shared ID across all 3 databases

### Main Operations
- **Retain**: Store memories → Thalamus Filter → Fact Extraction → Engram Creation → 3-DB Write
- **Recall**: Retrieve memories → Mode-aware MPFP → Qdrant Seeds + Neo4j Traversal → Construction
- **Reflect**: Deep analysis → Priority-based Reconsolidation → Schema Evolution

---

## Hindsight Memory Integration

### Memory Bank Configuration
- **Bank ID**: `m2-consulting-memory`
- **Operations**: `recall` (retrieve), `retain` (store), `reflect` (deep analysis)

### Session Protocol

**On Session Start** — Load context:
```
recall(bank_id="m2-consulting-memory", query="engram architecture current epic story task progress")
recall(bank_id="m2-consulting-memory", query="recent decisions patterns problems solutions")
recall(bank_id="m2-consulting-memory", query="preferences code_style workflow conventions")
```

**Before Implementation** — Check for prior knowledge:
```
recall(bank_id="m2-consulting-memory", query="[task-area] pattern solution approach best_practice")
recall(bank_id="m2-consulting-memory", query="[task-area] error problem anti_pattern avoid")
```

**After Task Completion** — Save learnings:
```
retain(bank_id="m2-consulting-memory", content="Task: [what]. Approach: [how]. Result: [outcome]. Key decisions: [why].", context="experience")
```

**On Error** — Check memory before fixing:
```
recall(bank_id="m2-consulting-memory", query="[error keywords] fix solution workaround")
```

**After Fix** — Save solution:
```
retain(bank_id="m2-consulting-memory", content="Error: [message]. Cause: [root cause]. Fix: [solution]. Prevention: [how to avoid].", context="error")
```

### Build Verification Gates

Before marking any task as done, verify:

```bash
# 1. Lint check
cd hindsight-api && uv run ruff check .

# 2. Type check
cd hindsight-api && uv run ty check hindsight_api/

# 3. Tests pass
cd hindsight-api && uv run pytest tests/ -x

# 4. Format check
cd hindsight-api && uv run ruff format --check .
```

All 4 gates must pass. If any fails, fix before proceeding.

---

## Epic Workflow

### Working Through the Backlog

Epics are in `docs/engram/backlog/` and must be worked **sequentially** (Epic 01 → 02 → ... → 15).

**For each Epic:**
1. Read `epic-NN-*/epic.md` for scope, dependencies, and acceptance criteria
2. Work stories sequentially within the epic
3. For each story, work tasks as the embedded checklist

**For each Task:**
1. Load memory context: `recall(bank_id="m2-consulting-memory", query="[task keywords]")`
2. Read the relevant source files in `hindsight-api/`
3. Implement the change
4. Run Build Verification Gates
5. Update the task checkbox in the story file
6. Save learnings: `retain(bank_id="m2-consulting-memory", ...)`

**For each Story completion:**
1. All tasks checked off
2. Build Verification Gates pass
3. Update story status in epic.md

**For each Epic completion:**
1. All stories done
2. Run Milestone acceptance criteria tests (see `milestones.md`)
3. Update epic status in `epic-overview.md`
4. Run `/milestone-check` if this completes a phase

### Test Policy (Staffeled)
- **Epic 01-02:** Unit-Tests + Connectivity-Tests
- **Ab Epic 05:** Integration-Tests (data flows through system)
- **Ab Epic 07:** Retrieval-Tests (Precision/Recall, Mode-Dependency)
- **Ab Epic 12:** Knowledge-Evolution-Tests + Benchmark B
- **Epic 15:** Benchmark C (Golden Dataset) for full validation

---

## Key Conventions

### Code Quality
**Always run the lint script after making Python changes:**
```bash
./scripts/hooks/lint.sh
```

### Memory Banks
- Each bank is an isolated memory store
- Banks have dispositions (skepticism, literalism, empathy: 1-5)
- Bank isolation is strict — no cross-bank data leakage

### Python Style
- Python 3.11+, type hints required
- Async throughout (asyncpg, async FastAPI)
- Pydantic models for request/response
- Ruff for linting (line-length 120)
- No Python files at project root

### TypeScript Style (Control Plane)
- Next.js App Router
- Tailwind CSS with shadcn/ui components

### Adding New API Configuration Flags

1. **config.py**: Add `ENV_*` + `DEFAULT_*` constants, field to `HindsightConfig`, init in `from_env()`
2. **main.py**: Add field to manual `HindsightConfig()` constructor
3. **Use**: `from ...config import get_config; config = get_config(); value = config.your_field`
4. **Docs**: Add to `hindsight-docs/docs/developer/configuration.md`

---

## Environment Setup

```bash
cp .env.example .env
# Edit .env with LLM API key

# Python deps
uv sync --directory hindsight-api/

# Node deps
npm install
```

Required env vars:
- `HINDSIGHT_API_LLM_PROVIDER`: openai, anthropic, gemini, groq, ollama, lmstudio
- `HINDSIGHT_API_LLM_API_KEY`: Your API key
- `HINDSIGHT_API_LLM_MODEL`: Model name

Optional:
- `HINDSIGHT_API_EMBEDDINGS_PROVIDER`: local (default) or tei
- `HINDSIGHT_API_RERANKER_PROVIDER`: local (default) or tei
- `HINDSIGHT_API_DATABASE_URL`: External PostgreSQL (uses embedded pg0 by default)

---

## Bio → Architecture Mapping (Quick Reference)

| Biology | Architecture |
|---------|-------------|
| Hippocampus | Pre-Engram Buffer + Engram Dictionary |
| Neocortex | Schema Store / Meta-Engrams |
| Thalamus | Thalamus Filter (4 scores) |
| Dentate Gyrus | Pattern Separation |
| CA3 | Pattern Completion |
| CA1 | Mismatch Detection (Novelty/Surprise) |
| SWS/Sharp-Wave Ripples | NCR Phase 1+2 |
| REM Sleep | NCR Phase 3 (Schema Compression) |
| PFC | Session + Working Context |
| Dopamine | Positive Prediction Error → Engram weight up |
| Noradrenaline | Surprise Score as plasticity multiplier |
| Cortisol | Stress flag throttles plasticity |
| LTP Early | Pre-Engram Buffer entry (fragile) |
| LTP Late | Consolidated Engram (after NCR) |
| STC | Association window in Pre-Engram Buffer |
