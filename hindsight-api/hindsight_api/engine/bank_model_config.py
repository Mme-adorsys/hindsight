"""
Bank Model Config — Per-bank LLM budget profile and per-step override management.

Each memory bank can be configured with a budget profile (Low/Mid/High) and
optional per-step model tier overrides. Configuration is persisted in PostgreSQL
and cached in-memory for 5 minutes per bank.

Priority chain for model resolution (highest first):
  1. Request-level override (handled by caller via BudgetProfile.with_override)
  2. Bank-level configuration (this module)
  3. Environment variable (handled by resolve_llm_config Level 1)
  4. Budget profile default (handled by resolve_llm_config Level 2)

Bio mapping: no direct bio analogue — this is pure operational configuration.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from .llm_routing import HIGH_BUDGET, LOW_BUDGET, MID_BUDGET, BudgetProfile, ModelTier, PipelineStep
from .utils import fq_table

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Profile name → BudgetProfile constant
PROFILE_MAP: dict[str, BudgetProfile] = {
    "low": LOW_BUDGET,
    "mid": MID_BUDGET,
    "high": HIGH_BUDGET,
}

# Cache TTL in seconds (5 minutes)
_CACHE_TTL: float = 300.0


class BankModelConfigRepo:
    """CRUD repository for per-bank LLM budget profile configuration.

    Configuration is loaded from PostgreSQL and cached in-memory with a 5-minute TTL.
    After a PUT (set_config), the cache entry is immediately invalidated.

    Usage::

        repo = BankModelConfigRepo()
        profile = await repo.get_profile(conn, bank_id)
        llm = resolve_llm_config("retain", "sequence_analysis", op, gl, budget_profile=profile)
    """

    def __init__(self) -> None:
        # bank_id → (BudgetProfile, expiry_timestamp)
        self._cache: dict[str, tuple[BudgetProfile, float]] = {}

    async def get_profile(self, conn: Any, bank_id: str) -> BudgetProfile:
        """Return the effective BudgetProfile for a bank, applying stored overrides.

        Loads from DB on cache miss or TTL expiry. Returns MID_BUDGET for banks
        with no stored configuration.

        Args:
            conn: asyncpg connection or pool connection.
            bank_id: The target memory bank.

        Returns:
            BudgetProfile with base profile + any per-step overrides applied.
        """
        now = time.monotonic()
        cached = self._cache.get(bank_id)
        if cached is not None:
            profile, expiry = cached
            if now < expiry:
                return profile

        # Cache miss or expired — load from DB
        row = await conn.fetchrow(
            f"SELECT budget_profile, step_overrides FROM {fq_table('bank_model_config')} WHERE bank_id = $1",
            bank_id,
        )

        if row is None:
            profile = MID_BUDGET
        else:
            base = PROFILE_MAP.get(row["budget_profile"], MID_BUDGET)
            overrides: dict = row["step_overrides"] or {}
            profile = _apply_overrides(base, overrides)

        self._cache[bank_id] = (profile, now + _CACHE_TTL)
        return profile

    async def set_config(
        self,
        conn: Any,
        bank_id: str,
        budget_profile: str | None = None,
        step_overrides: dict[str, str] | None = None,
    ) -> None:
        """Upsert budget profile and/or step overrides for a bank.

        Args:
            conn: asyncpg connection.
            bank_id: The target memory bank.
            budget_profile: Profile name — "low", "mid", or "high". If None, keeps existing.
            step_overrides: Dict of {PipelineStep.value → ModelTier.value}. If None, keeps existing.

        Raises:
            ValueError: If budget_profile or any override key/value is invalid.
        """
        if budget_profile is not None and budget_profile not in PROFILE_MAP:
            raise ValueError(f"Invalid budget_profile {budget_profile!r}. Must be one of: {list(PROFILE_MAP)}")

        if step_overrides is not None:
            _validate_overrides(step_overrides)

        # Build upsert: load existing first, then merge
        existing = await conn.fetchrow(
            f"SELECT budget_profile, step_overrides FROM {fq_table('bank_model_config')} WHERE bank_id = $1",
            bank_id,
        )

        if existing is None:
            new_profile = budget_profile or "mid"
            new_overrides = step_overrides or {}
        else:
            new_profile = budget_profile if budget_profile is not None else existing["budget_profile"]
            if step_overrides is not None:
                existing_overrides = dict(existing["step_overrides"] or {})
                existing_overrides.update(step_overrides)
                new_overrides = existing_overrides
            else:
                new_overrides = existing["step_overrides"] or {}

        import json

        await conn.execute(
            f"""
            INSERT INTO {fq_table("bank_model_config")} (bank_id, budget_profile, step_overrides, updated_at)
            VALUES ($1, $2, $3::jsonb, NOW())
            ON CONFLICT (bank_id) DO UPDATE
                SET budget_profile = EXCLUDED.budget_profile,
                    step_overrides = EXCLUDED.step_overrides,
                    updated_at     = NOW()
            """,
            bank_id,
            new_profile,
            json.dumps(new_overrides),
        )

        # Invalidate cache immediately after write
        self._cache.pop(bank_id, None)
        logger.debug(
            "bank_model_config: updated bank_id=%s profile=%s overrides=%s", bank_id, new_profile, new_overrides
        )

    async def get_config_row(self, conn: Any, bank_id: str) -> dict:
        """Return raw config row for Admin API serialisation.

        Returns the stored profile name, overrides dict, and the effective tier
        per PipelineStep (for debugging / CP display).

        Args:
            conn: asyncpg connection.
            bank_id: The target memory bank.

        Returns:
            Dict with keys: budget_profile (str), step_overrides (dict),
            effective_tiers (dict[str, str]).
        """
        row = await conn.fetchrow(
            f"SELECT budget_profile, step_overrides FROM {fq_table('bank_model_config')} WHERE bank_id = $1",
            bank_id,
        )

        if row is None:
            profile_name = "mid"
            overrides: dict = {}
        else:
            profile_name = row["budget_profile"]
            overrides = dict(row["step_overrides"] or {})

        base = PROFILE_MAP.get(profile_name, MID_BUDGET)
        effective = _apply_overrides(base, overrides)

        return {
            "budget_profile": profile_name,
            "step_overrides": overrides,
            "effective_tiers": {step.value: effective.get_tier(step).value for step in PipelineStep},
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_overrides(base: BudgetProfile, overrides: dict[str, str]) -> BudgetProfile:
    """Apply a {step_name: tier_name} dict to a BudgetProfile, returning a new instance."""
    profile = base
    for step_val, tier_val in overrides.items():
        try:
            step = PipelineStep(step_val)
            tier = ModelTier(tier_val)
        except ValueError:
            logger.warning("bank_model_config: ignoring invalid override %r → %r", step_val, tier_val)
            continue
        profile = profile.with_override(step, tier)
    return profile


def _validate_overrides(overrides: dict[str, str]) -> None:
    """Raise ValueError if any key or value in overrides is not a valid enum entry."""
    valid_steps = {s.value for s in PipelineStep}
    valid_tiers = {t.value for t in ModelTier}
    for key, val in overrides.items():
        if key not in valid_steps:
            raise ValueError(f"Invalid pipeline step {key!r}. Valid steps: {sorted(valid_steps)}")
        if val not in valid_tiers:
            raise ValueError(f"Invalid model tier {val!r}. Valid tiers: {sorted(valid_tiers)}")
