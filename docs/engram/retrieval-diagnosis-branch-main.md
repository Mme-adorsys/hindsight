# Retrieval Diagnosis — "branch" / "main" Not Found

**Date:** 2026-04-22
**Bank under test:** originally reported as `memory-engram-dev`; actual bank name is `marcel-engram-dev`
**Query under test:** `"branch"` (cross-checked with `"main"` and `"welcher name hat mein branch"`)
**Tool:** `hindsight/hindsight-dev/hindsight_dev/diagnose_recall.py`
**Modes tested:** all four (precision, exploration, analogy, validation)

---

## TL;DR

Two independent problems, stacked:

1. **Bank-name typo in the user's query** — the bank is `marcel-engram-dev`, not `memory-engram-dev`. Any recall against the wrong name returns empty trivially.
2. **Cross-encoder kills otherwise-perfect BM25 matches.** Against the correct bank, BM25 finds all four relevant engrams. The reranker assigns them scores of **−3.3 to −3.8**, while every mode's `ce_min_score` is in `[0.01, 0.05]`. Negative is not just "below threshold" — it is a strong "irrelevant" vote from the model, so even Exploration mode drops them.

The pipeline has no path for a BM25-strong / CE-weak candidate to survive. That is the structural defect.

---

## Evidence

### Global inventory (step 0)

| schema | bank_id | count |
|---|---|---|
| public | `marcel-engram-dev` | 143 |
| public | `integration_test_bank` | 36 |

`memory-engram-dev` does not exist in any schema. ILIKE `'%branch%'` matches 4 rows, all in `marcel-engram-dev`.

### BM25 — works perfectly (step 3)

`tsquery = branch` against `marcel-engram-dev` returns the four relevant engrams:

| id | `ts_rank_cd` | snippet |
|---|---|---|
| c5cfe59b… | 0.2000 | "Der Standard Git-Branch-Name wurde von 'master' auf 'main' geändert…" |
| 240934d4… | 0.1000 | "Neue Repositories erhalten automatisch 'main' als Standard-Branch-Name…" |
| d9cc548b… | 0.1000 | "Der Standard-Branch-Name wird automatisch bei neuen Repositories umgestellt…" |
| 85dcf94d… | 0.1000 | "Ab Git Version 2.28 wird der Standard-Branch-Name 'main' automatisch für neue Repositories verwendet…" |

tsvector tokens confirm `'simple'` correctly splits hyphenated compounds: `'standard-branch-name'` is stored alongside `'standard'`, `'branch'`, `'name'` — so the query token `branch` hits as expected.

### Semantic — too weak for a single-token query (step 4)

Top-10 cosine similarity for the query embedding of `"branch"` (no threshold):

- Rank 1: `0.272` — unrelated Saga Pattern engram
- Rank 10: `0.154` — unrelated Event Sourcing engram
- **None of the four branch engrams are in the top 10.** Their similarity is below 0.154.

All mode thresholds (`0.50` – `0.70`) are far above even the highest semantic score. Semantic search contributes nothing here.

### Cross-encoder — kills the BM25 matches (step 5b)

Local cross-encoder (`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` per `config.py:131`) scores on the exact BM25 matches:

| id | BM25 | CE |
|---|---|---|
| c5cfe59b… | 0.2000 | **−3.6476** |
| 240934d4… | 0.1000 | **−3.8113** |
| d9cc548b… | 0.1000 | **−3.3053** |
| 85dcf94d… | 0.1000 | **−3.7643** |

Thresholds in `RECALL_MODE_CONFIG`: precision `0.05`, validation `0.03`, analogy `0.02`, exploration `0.01`. All four engrams fail every threshold.

This contradicts the comment block at `engine/recall_orchestrator.py:59-66`, which claims the multilingual mmarco model produces scores in `[0.0, 0.5]` for relevant matches. The empirical range on this setup is negative logits — likely because the model is invoked without a sigmoid activation, so raw logits pass through. Either the model, the output handling, or the threshold calibration no longer matches the comment.

### recall_async — 0 results in every mode (step 6)

All four modes (precision, exploration, analogy, validation) returned 0 results against the correct bank with the query `"branch"`. Trace confirms the pipeline ran end-to-end — candidates were found but filtered out.

---

## Cause attribution

Two of the four hypotheses from the plan are confirmed; the other two are ruled out.

| # | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| 1 | BM25 tokenization mis-splits German compounds | **Ruled out.** `to_tsvector('simple', ...)` correctly yields `standard`, `branch`, `name`, and BM25 finds all relevant engrams. | step 2, step 3 |
| 2 | Cross-encoder threshold drops matches | **CONFIRMED (primary cause).** CE logits ≈ −3.5, thresholds ≥ 0.01. Every mode rejects. | step 5b |
| 3 | Memory is in a different bank | **CONFIRMED (trigger for the user-visible empty result).** Bank is `marcel-engram-dev`, not `memory-engram-dev`. | step 0 |
| 4 | Engram not in index at all | **Ruled out.** 143 engrams indexed; 4 contain "branch". | step 0 |

---

## Recommendations for the follow-up fix plan

Scope, not implementation. The fix plan should pick among these — they are not cumulative.

1. **Calibrate CE output.** Either switch the reranker to sigmoid/logistic output so scores land in `[0, 1]` as the docstring promises, or recalibrate `ce_min_score` to the empirical range observed for the installed model. The current `0.01` is not the "low cutoff" the concept assumes — it is effectively "reject almost everything" for this setup.

2. **Add a BM25-safety rescue.** When CE would reject every candidate, fall back to the top-N by BM25 rank without CE filtering. Plain keyword presence is a strong enough signal to not return empty, even when CE disagrees. Ensures the Precision-mode concept promise ("exact Engram match") is structurally reachable.

3. **Wire Context-Tag Overlap into scoring.** The concept (`11_retrieval_architecture.md`, §3.2) weights tag overlap highly for Precision. The code stores tags in `engram_dictionary.tags` but only uses them as an AND filter. A tag-match bonus (or a shortlist path that returns tag-matching engrams unconditionally) closes the biggest Concept/Implementation gap surfaced here.

4. **Diagnose the bank-name issue upstream.** The user observed empty results partly because of a typo. Consider: a `list_banks` / `ping_bank` MCP tool, or a warning from `recall` when the named bank has zero engrams. Silent-empty is a bad failure mode.

Options 1 or 2 together address the actual retrieval defect. Option 3 addresses the underlying architectural gap. Option 4 addresses the UX that made the defect hard to spot.

---

## Reproduction

```bash
cd hindsight && set -a && source .env && set +a && cd hindsight-dev && \
  uv run python -m hindsight_dev.diagnose_recall \
    --bank marcel-engram-dev --query "branch"
```

Swap `--query "main"` or `--query "welcher name hat mein branch"` to cross-check. All three queries reproduce the CE-rejection pattern.
