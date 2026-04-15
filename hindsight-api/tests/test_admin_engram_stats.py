"""
Engram Lifecycle Stats — layer normalization tests.

Guards the ``get_engram_stats`` aggregation contract used by the CP
Engram Lifecycle view. Writes three rows directly into ``engram_dictionary``
covering the three layer values that co-exist in production:

  * ``layer = NULL``       — legacy Working-Memory entries
  * ``layer = 'working'``  — Epic 24 naming, written by the retain pipeline
                              (``fact_storage.py``, ``observation_regeneration.py``)
  * ``layer = 'buffer'``   — post-Consolidation 1 promotion

The stats endpoint must normalize both ``NULL`` and ``'working'`` to the
``working_memory`` bucket so the UI kärtchen reflects the data correctly.
Missing this normalization previously made Working Memory appear as 0
in the Engram Lifecycle view even though rows existed.
"""

from __future__ import annotations

import uuid

import pytest


@pytest.mark.asyncio
async def test_get_engram_stats_normalizes_working_layer(memory, request_context):
    """Both ``layer IS NULL`` and ``layer = 'working'`` count as working_memory."""
    bank_id = f"stats-layer-test-{uuid.uuid4().hex[:8]}"

    # Ensure bank exists so FK constraints pass.
    async with memory._pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO banks (bank_id, name, disposition, background)
            VALUES ($1, $1, '{"skepticism": 3, "literalism": 3, "empathy": 3}'::jsonb, '')
            ON CONFLICT (bank_id) DO NOTHING
            """,
            bank_id,
        )

        # Three rows covering all Working-Memory and Buffer layer representations.
        wm_null_id = uuid.uuid4()
        wm_working_id = uuid.uuid4()
        buffer_id = uuid.uuid4()

        await conn.execute(
            """
            INSERT INTO engram_dictionary
                (engram_id, bank_id, strength, layer, status)
            VALUES
                ($1, $4, 0.20, NULL,      'active'),
                ($2, $4, 0.30, 'working', 'active'),
                ($3, $4, 0.60, 'buffer',  'active')
            """,
            wm_null_id,
            wm_working_id,
            buffer_id,
            bank_id,
        )

    try:
        stats = await memory.get_engram_stats(bank_id, request_context=request_context)
    finally:
        # Cleanup regardless of assertion outcome.
        async with memory._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM engram_dictionary WHERE bank_id = $1",
                bank_id,
            )
            await conn.execute("DELETE FROM banks WHERE bank_id = $1", bank_id)

    layers = stats["layers"]

    # Core contract: NULL + 'working' both land in working_memory.
    assert layers["working_memory"]["count"] == 2, (
        f"expected both NULL and 'working' rows in working_memory bucket, got {layers}"
    )
    assert layers["buffer"]["count"] == 1
    assert layers["neocortex"]["count"] == 0
    assert stats["total"] == 3

    # avg_strength for working_memory should be the mean of the two WM rows.
    assert layers["working_memory"]["avg_strength"] == pytest.approx(0.25, abs=1e-6)
    assert layers["buffer"]["avg_strength"] == pytest.approx(0.60, abs=1e-6)


@pytest.mark.asyncio
async def test_get_engram_stats_empty_bank_returns_zero_counts(memory, request_context):
    """Empty bank must return all three buckets with count=0 instead of raising."""
    bank_id = f"stats-empty-{uuid.uuid4().hex[:8]}"

    async with memory._pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO banks (bank_id, name, disposition, background)
            VALUES ($1, $1, '{"skepticism": 3, "literalism": 3, "empathy": 3}'::jsonb, '')
            ON CONFLICT (bank_id) DO NOTHING
            """,
            bank_id,
        )

    try:
        stats = await memory.get_engram_stats(bank_id, request_context=request_context)
    finally:
        async with memory._pool.acquire() as conn:
            await conn.execute("DELETE FROM banks WHERE bank_id = $1", bank_id)

    assert stats["total"] == 0
    assert stats["layers"]["working_memory"]["count"] == 0
    assert stats["layers"]["buffer"]["count"] == 0
    assert stats["layers"]["neocortex"]["count"] == 0
