# Architecture

## System boundary

The project is a localhost dashboard and a set of Python modules. It does not expose a public server and does not run unattended platform jobs. Connected platform reads and Golden revalidation are operator-triggered. Interaction and formal publishing remain blocked.

## Main flow

```text
┌──────────────────────────────┐
│ Operator-triggered evidence  │
│ A1/A2 account + B1 platform  │
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ Human Golden checkpoint      │
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ B3 Golden engine             │
│ Candidate → Feature → Sample │
│ → Pattern                    │
└──────────────┬───────────────┘
               ↓ optional explicit click
┌──────────────────────────────┐
│ Revalidation snapshots       │
│ growth → pattern weight      │
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ Topic score commit           │
│ account 35 / platform 30     │
│ golden 30 / novelty 5        │
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ Topic → Content → Outline    │
│ → Cover → Reviewer           │
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ Local visual production      │
│ no-text assets + typography  │
│ JPG previews + editable PPTX │
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ Manual Canva edit and        │
│ official-client publishing   │
└──────────────────────────────┘
```

## Modules

| Directory | Responsibility |
| --- | --- |
| `collect/` | OpenCLI adapters for visible account and platform evidence |
| `analyze/` | Topic evidence, heat, account intelligence and official score commit |
| `golden/` | Candidate hydration, Feature analysis, admission, Pattern and revalidation |
| `agents/` | Topic, Content, Outline, Cover, Reviewer and orchestration contracts |
| `personal_ops/` | Local voice memory, daily decisions and workflow coordinator |
| `content/` | Drafts, no-text asset planning, local rendering and editable PPTX output |
| `canva_connect/` | Optional Canva OAuth and Design Import handoff |
| `compliance/` | Text policy and platform operation policy |
| `dashboard/` | Local Flask services, task queue and single-page interface |
| `schemas/` | Structured LLM and carousel output contracts |
| `skills/` | Repository-specific Codex workflow instructions |

## Evidence layers

The system intentionally separates observed data from inference:

- account evidence: visible creator-center totals and attributable per-note metrics;
- platform evidence: operator-triggered search/detail reads;
- Golden evidence: canonical source copy, metrics, extracted Features and admission result;
- revalidation evidence: later heat snapshots and growth calculations;
- transfer evidence: attributable performance after a Pattern is used;
- personal voice evidence: owned/authorized writing used for style only.

Missing metrics remain unknown and are never silently converted to zero. A cached sample is not described as real-time.

## Storage

SQLite and JSON runtime state live under `data/`. Generated drafts and media live under `content/`. These paths are excluded from Git because they can contain account analytics, source text, personal writing, unpublished drafts and generated assets.

Fresh clones start with `config/topic_weights.example.json`. On first read it is copied to ignored runtime state at `data/topic_weights.json`, so account-derived scores cannot be accidentally committed with the public seed. Database schemas are created lazily when the corresponding local action runs.

## Model routing

`golden/llm.py` provides two backend families:

- `http`: OpenAI-compatible Base URL with an operator-supplied key;
- `codex_cli`: locally authenticated Codex CLI with structured schemas and read-only text execution.

Routing can be overridden by role. Deterministic calculations remain in Python; the LLM interprets supplied evidence and creates structured content.

## Visual pipeline

```text
Content knowledge_units
  → Outline modules
  → Visual Director
  → no-text asset tasks
  → local 1080×1440 renderer
  → visual review
  → layered PPTX builder
```

The image worker does not own Chinese typography. The editable PPTX keeps text boxes, shapes and images separate where practical. Canva Connect is an optional explicit upload; manual PPTX import is the default portable handoff.

## Failure behavior

- Missing OpenCLI: local dashboard/generation remains available; connected reads fail with an actionable error.
- Missing LLM: evidence pages remain readable; generation actions refuse to invent results.
- Missing ImageGen: local layout renders without model-generated artwork.
- Missing Playwright browser: renderer falls back to Pillow where supported.
- Incomplete B3 evidence: the previous committed Topic Score remains unchanged.
- Login, verification, rate limit or security warning: stop the current connected batch and require manual resolution.
