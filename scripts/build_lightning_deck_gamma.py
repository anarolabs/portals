#!/usr/bin/env python3
"""Lightning talk deck builder — Gamma route.

Sends the 13-slide content to Gamma's Generations API and polls for the
finished URL. Uses textMode=preserve so Roman's copy isn't rewritten;
cardSplit=inputTextBreaks so `---` separators define slide boundaries.

Env:
    GAMMA_API_KEY  — required, set in ~/.zshrc

Run:
    bash "/Users/romansiepelmeyer/Documents/Claude Code/portals/run.sh" \
        scripts/build_lightning_deck_gamma.py
"""
import json
import os
import sys
import time
from pathlib import Path

# Gamma's HTTP client — using urllib to avoid extra deps
import urllib.request
import urllib.error

# --- 1Password-backed credentials resolver ---
import sys as _cred_sys
import os as _cred_os
_cred_sys.path.insert(0, _cred_os.path.expanduser("~/Documents/Claude Code/knowledge-graph/03-ingestion"))
from credentials import get as _get_cred  # noqa: E402


GAMMA_BASE = "https://public-api.gamma.app/v0.2"
GAMMA_BASE_V1 = "https://public-api.gamma.app/v1.0"
POLL_INTERVAL_SEC = 5
POLL_MAX_ATTEMPTS = 60  # 5 minutes


# ============================================================================
# The slide content. 13 slides, `---` between each.
# ============================================================================
CONTENT = """\
# Toward ambient intelligence

A knowledge graph journey.

Roman Siepelmeyer · Anaro Labs

---

# A lot of context switching

- AI roadmap for a customer service team
- Four growth & culture stakeholders, four different topics
- Co-design call for my startup's first test customer
- Two languages. Four companies.

**Every AI call started from zero.**

---

# The models are good enough

The gap is persistent structured memory.

I'm team **scaffolding**.

---

# Markdown broke at scale

- Summary file plus folder structure. Worked early.
- 150k-200k tokens to seed a complex engagement.
- No temporality. No concept linking. Bloat killed retrieval.
- I vibe-coded a knowledge graph. Interesting. Not enough.

---

# Build the ontology first

- **Schema.** Entities and the relationships between them.
- **Sensors.** Where the system listens. Email, Slack, transcripts, calendar.
- **Logic.** Judgment rules. My co-founder is technical lead. His product call is a decision. Mine is a suggestion.
- **Retrieval.** Pre-shaped queries wrapped as MCP tools.

---

# Retrieval, in three steps

- **Keyword search.** Find the relevant nodes in the graph (BM25-style).
- **Embedding search.** Voyage embeddings + sqlite-vec, scoped to those nodes. Catches paraphrases.
- **Hydration.** Pull the markdown sections each node points to. That's what Claude actually reads.

---

# Prometheus today

**Stack:** Claude Code · Anthropic models · Neo4j · Voyage embeddings · Railway

- **Storage.** Neo4j on Railway. Beat Postgres+pgvector and Memgraph on traversal cost and Cypher tooling.
- **Ingestion.** House of Prometheus — 11 named subagents, each owns one job.
- **Retrieval.** Keyword + Voyage + hydration. Six policies as MCP tools.
- **Observability.** Athena trace on every call.

---

# The House of Prometheus

The pipeline that runs today.

*[Diagram: Sources → Sentinel → Steward-orchestrated workers (Clerk, Scribe, Author, Foreman, Cartographer, Librarian, Custodian, Herald) → Neo4j graph + Markdown patches + Linear sync + Daily digest. Athena trace on every call.]*

---

# The graph, in 3D

Every node. Type a query. Watch what fires.

*[Live demo — Atlas]*

---

# What changed

- **Client stories.** 80% of the report drafted before the call, not after.
- **Cross-engagement pattern detection.** I see what's recurring across clients.
- **Pre-call prep** without re-reading my notes.
- **Sources I read** map onto the work, not a folder I'll never reopen.

---

# Where it still breaks

- Silent retrieval failures. Sometimes the right context just doesn't surface.
- Multiplayer doesn't work. My co-founders can't write into the graph the way I do.
- Ingestion is on a 45-minute cron. Push capture would matter.
- Skills are locked to Claude Code. Other agent platforms re-implement from scratch.

---

# From memory to ontology

A machine that understands my world clearly enough to brief any agent that asks.

*[Vision diagram: Sensors → Ontology (the semantic core) → Agents (Claude Code, Primo, Calliope, Hephaestus, future agents).]*

---

# Models are good enough. The scaffolding is the work.

Ambient intelligence is the layer underneath every AI tool I use.

Day one and day thirty stop looking the same.
"""


# ============================================================================
# Gamma API helpers
# ============================================================================
def _http(method: str, path: str, api_key: str, body: dict | None = None) -> dict:
    url = f"{GAMMA_BASE_V1}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Cloudflare blocks Python-urllib's default UA; use a browser signature.
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code} {e.reason}: {body_text}", file=sys.stderr)
        raise
    except urllib.error.URLError as e:
        print(f"Network error: {e}", file=sys.stderr)
        raise


def create_generation(api_key: str) -> str:
    body = {
        "inputText": CONTENT,
        "format": "presentation",
        "textMode": "preserve",          # don't rewrite Roman's copy
        "cardSplit": "inputTextBreaks",  # honor `---` as slide separators
        "textOptions": {
            "amount": "brief",           # v1.0 enum: brief|medium|detailed|extensive
            "tone": "direct, practitioner, no hype",
            "audience": "engineering-leaning AI community",
            "language": "en",
        },
        "imageOptions": {
            "source": "noImages",        # text-only deck per design brief
        },
        "cardOptions": {
            "dimensions": "16x9",
        },
        "sharingOptions": {
            "workspaceAccess": "edit",   # Roman owns + can edit
            "externalAccess": "noAccess",
        },
        "additionalInstructions": (
            "Minimalist Apple keynote aesthetic. Pure white background. "
            "Near-black text. Single purple accent (#7C5CFC) used sparingly, "
            "max one element per slide. Sans-serif typography (Inter or "
            "similar). Lots of whitespace. No decorative shapes, no icons, "
            "no gradients. Hero typography on title and statement slides. "
            "Headlines in sentence case."
        ),
    }
    print("POST /generations …", file=sys.stderr)
    result = _http("POST", "/generations", api_key, body=body)
    return result["generationId"]


def poll_generation(api_key: str, gen_id: str) -> dict:
    for attempt in range(POLL_MAX_ATTEMPTS):
        result = _http("GET", f"/generations/{gen_id}", api_key)
        status = result.get("status")
        print(f"  attempt {attempt + 1}: status={status}", file=sys.stderr)
        if status == "completed":
            return result
        if status == "failed":
            raise RuntimeError(f"Generation failed: {result}")
        time.sleep(POLL_INTERVAL_SEC)
    raise TimeoutError(f"Generation {gen_id} did not complete within "
                       f"{POLL_INTERVAL_SEC * POLL_MAX_ATTEMPTS}s")


# ============================================================================
# Main
# ============================================================================
def main():
    try:
        api_key = _get_cred("gamma")["api_key"]
    except Exception:
        api_key = os.environ.get("GAMMA_API_KEY")
    if not api_key:
        print("ERROR: GAMMA_API_KEY not set. Add to ~/.zshrc and reopen shell, "
              "or pass it inline: GAMMA_API_KEY=sk-gamma-... bash run.sh "
              "scripts/build_lightning_deck_gamma.py", file=sys.stderr)
        sys.exit(1)

    gen_id = create_generation(api_key)
    print(f"Generation queued: {gen_id}", file=sys.stderr)

    result = poll_generation(api_key, gen_id)
    url = result.get("gammaUrl") or result.get("url") or result.get("documentUrl")

    print(json.dumps({
        "generation_id": gen_id,
        "url": url,
        "status": result.get("status"),
        "credits_used": result.get("credits", {}),
    }, indent=2))


if __name__ == "__main__":
    main()
