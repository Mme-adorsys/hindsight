"""
Deterministic fixture data for Epic 07 Retrieval Integration Tests (Story 06).

Design:
  20 engrams total — 10 in AGENT_BANK, 10 in SHARED_BANK
  4 topic clusters: tech/ai (5), science (5), history (5), art (5)
  384-dim embeddings computed via numpy seed=42 + topic-cluster offsets

Ground truth for Precision/Recall assertions:
  Query cluster "tech" → ground truth = engrams tagged ["tech", "ai"]
  Query cluster "science" → ground truth = engrams tagged ["science"]
  etc.

Banks:
  AGENT_BANK  = "retrieval-test-agent"   — personal/episodic knowledge
  SHARED_BANK = "retrieval-test-shared"  — cross-domain semantic knowledge
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Bank identifiers used in integration tests
# ---------------------------------------------------------------------------
AGENT_BANK = "retrieval-test-agent"
SHARED_BANK = "retrieval-test-shared"
ALL_TEST_BANKS = [AGENT_BANK, SHARED_BANK]

# ---------------------------------------------------------------------------
# Embedding utilities
# ---------------------------------------------------------------------------
EMBEDDING_DIM = 384
_RNG = np.random.default_rng(seed=42)

# One cluster center per topic (384-dim, normalized)
_TOPIC_CENTERS: dict[str, np.ndarray] = {}


def _make_center(seed_offset: int) -> np.ndarray:
    rng = np.random.default_rng(seed=42 + seed_offset)
    v = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


for _i, _topic in enumerate(["tech", "science", "history", "art"]):
    _TOPIC_CENTERS[_topic] = _make_center(_i * 100)


def cluster_embedding(topic: str, member_index: int) -> list[float]:
    """Return a 384-dim float list near the topic cluster center (deterministic)."""
    center = _TOPIC_CENTERS[topic]
    rng = np.random.default_rng(seed=42 + hash(topic) % 1000 + member_index)
    noise = rng.standard_normal(EMBEDDING_DIM).astype(np.float32) * 0.05
    v = center + noise
    v = v / np.linalg.norm(v)
    return v.tolist()


def query_embedding(topic: str) -> list[float]:
    """Return the cluster center embedding for a topic — ideal query vector."""
    return _TOPIC_CENTERS[topic].tolist()


# ---------------------------------------------------------------------------
# Engram fixture dataclass
# ---------------------------------------------------------------------------


@dataclass
class EngramFixture:
    """
    Complete fixture for one Engram — inserted into PostgreSQL, Qdrant, Neo4j.
    """

    id: str  # UUID string
    bank_id: str
    text: str
    topic: str  # "tech" | "science" | "history" | "art"
    tags: list[str]
    strength: float
    novelty: float
    surprise: float
    task_relevance: float
    emotional_valence: float
    embedding: list[float] = field(default_factory=list)
    neo4j_links: list[str] = field(default_factory=list)  # engram IDs this links to via SIMILAR_TO


# ---------------------------------------------------------------------------
# Fixture data — 20 engrams
# ---------------------------------------------------------------------------

# Deterministic UUIDs via uuid5 with a fixed namespace
_NS = uuid.UUID("12345678-1234-5678-1234-567812345678")


def _uid(name: str) -> str:
    return str(uuid.uuid5(_NS, name))


ENGRAMS: list[EngramFixture] = [
    # -----------------------------------------------------------------------
    # Tech/AI — 5 engrams (3 agent, 2 shared)
    # -----------------------------------------------------------------------
    EngramFixture(
        id=_uid("tech-01"),
        bank_id=AGENT_BANK,
        text="Machine learning algorithms can identify patterns in large datasets automatically.",
        topic="tech",
        tags=["tech", "ai", "machine-learning"],
        strength=0.85,
        novelty=0.6,
        surprise=0.4,
        task_relevance=0.9,
        emotional_valence=0.1,
    ),
    EngramFixture(
        id=_uid("tech-02"),
        bank_id=AGENT_BANK,
        text="Neural networks mimic the structure of the human brain to process information.",
        topic="tech",
        tags=["tech", "ai", "neural-networks"],
        strength=0.80,
        novelty=0.7,
        surprise=0.5,
        task_relevance=0.85,
        emotional_valence=0.0,
    ),
    EngramFixture(
        id=_uid("tech-03"),
        bank_id=AGENT_BANK,
        text="Transformer architectures have revolutionized natural language processing tasks.",
        topic="tech",
        tags=["tech", "ai", "nlp"],
        strength=0.75,
        novelty=0.8,
        surprise=0.6,
        task_relevance=0.80,
        emotional_valence=0.0,
    ),
    EngramFixture(
        id=_uid("tech-04"),
        bank_id=SHARED_BANK,
        text="Reinforcement learning enables agents to learn optimal strategies through rewards.",
        topic="tech",
        tags=["tech", "ai", "reinforcement-learning"],
        strength=0.70,
        novelty=0.5,
        surprise=0.3,
        task_relevance=0.75,
        emotional_valence=0.0,
    ),
    EngramFixture(
        id=_uid("tech-05"),
        bank_id=SHARED_BANK,
        text="Vector databases store high-dimensional embeddings for semantic similarity search.",
        topic="tech",
        tags=["tech", "databases", "embeddings"],
        strength=0.65,
        novelty=0.6,
        surprise=0.4,
        task_relevance=0.70,
        emotional_valence=0.0,
    ),
    # -----------------------------------------------------------------------
    # Science — 5 engrams (2 agent, 3 shared)
    # -----------------------------------------------------------------------
    EngramFixture(
        id=_uid("science-01"),
        bank_id=AGENT_BANK,
        text="CRISPR-Cas9 allows precise editing of DNA sequences in living organisms.",
        topic="science",
        tags=["science", "biology", "genetics"],
        strength=0.80,
        novelty=0.9,
        surprise=0.8,
        task_relevance=0.70,
        emotional_valence=0.2,
    ),
    EngramFixture(
        id=_uid("science-02"),
        bank_id=AGENT_BANK,
        text="Quantum entanglement demonstrates non-local correlations between particles.",
        topic="science",
        tags=["science", "physics", "quantum"],
        strength=0.75,
        novelty=0.8,
        surprise=0.7,
        task_relevance=0.65,
        emotional_valence=0.1,
    ),
    EngramFixture(
        id=_uid("science-03"),
        bank_id=SHARED_BANK,
        text="The human microbiome consists of trillions of microorganisms influencing health.",
        topic="science",
        tags=["science", "biology", "microbiome"],
        strength=0.70,
        novelty=0.7,
        surprise=0.6,
        task_relevance=0.60,
        emotional_valence=0.1,
    ),
    EngramFixture(
        id=_uid("science-04"),
        bank_id=SHARED_BANK,
        text="Dark matter constitutes approximately 27% of the universe's total mass-energy.",
        topic="science",
        tags=["science", "physics", "cosmology"],
        strength=0.65,
        novelty=0.8,
        surprise=0.7,
        task_relevance=0.55,
        emotional_valence=0.0,
    ),
    EngramFixture(
        id=_uid("science-05"),
        bank_id=SHARED_BANK,
        text="Photosynthesis converts light energy into chemical energy stored in glucose.",
        topic="science",
        tags=["science", "biology", "chemistry"],
        strength=0.60,
        novelty=0.4,
        surprise=0.2,
        task_relevance=0.50,
        emotional_valence=0.0,
    ),
    # -----------------------------------------------------------------------
    # History — 5 engrams (3 agent, 2 shared)
    # -----------------------------------------------------------------------
    EngramFixture(
        id=_uid("history-01"),
        bank_id=AGENT_BANK,
        text="The Roman Empire reached its greatest territorial extent under Emperor Trajan.",
        topic="history",
        tags=["history", "rome", "ancient"],
        strength=0.75,
        novelty=0.3,
        surprise=0.2,
        task_relevance=0.60,
        emotional_valence=0.0,
    ),
    EngramFixture(
        id=_uid("history-02"),
        bank_id=AGENT_BANK,
        text="The Silk Road connected China with the Mediterranean world for centuries.",
        topic="history",
        tags=["history", "trade", "ancient"],
        strength=0.70,
        novelty=0.4,
        surprise=0.3,
        task_relevance=0.55,
        emotional_valence=0.1,
    ),
    EngramFixture(
        id=_uid("history-03"),
        bank_id=AGENT_BANK,
        text="The Renaissance began in Florence during the 14th century and spread across Europe.",
        topic="history",
        tags=["history", "renaissance", "europe"],
        strength=0.65,
        novelty=0.3,
        surprise=0.2,
        task_relevance=0.50,
        emotional_valence=0.2,
    ),
    EngramFixture(
        id=_uid("history-04"),
        bank_id=SHARED_BANK,
        text="The printing press revolutionized information dissemination in 15th century Europe.",
        topic="history",
        tags=["history", "technology", "europe"],
        strength=0.60,
        novelty=0.5,
        surprise=0.4,
        task_relevance=0.55,
        emotional_valence=0.1,
    ),
    EngramFixture(
        id=_uid("history-05"),
        bank_id=SHARED_BANK,
        text="The Industrial Revolution transformed manufacturing and society from the 18th century.",
        topic="history",
        tags=["history", "technology", "economy"],
        strength=0.55,
        novelty=0.4,
        surprise=0.3,
        task_relevance=0.50,
        emotional_valence=0.0,
    ),
    # -----------------------------------------------------------------------
    # Art — 5 engrams (2 agent, 3 shared)
    # -----------------------------------------------------------------------
    EngramFixture(
        id=_uid("art-01"),
        bank_id=AGENT_BANK,
        text="Impressionism captures fleeting moments of light and atmosphere in painting.",
        topic="art",
        tags=["art", "painting", "impressionism"],
        strength=0.70,
        novelty=0.4,
        surprise=0.3,
        task_relevance=0.40,
        emotional_valence=0.4,
    ),
    EngramFixture(
        id=_uid("art-02"),
        bank_id=AGENT_BANK,
        text="Baroque architecture features elaborate ornamentation and dramatic contrasts.",
        topic="art",
        tags=["art", "architecture", "baroque"],
        strength=0.65,
        novelty=0.3,
        surprise=0.2,
        task_relevance=0.35,
        emotional_valence=0.3,
    ),
    EngramFixture(
        id=_uid("art-03"),
        bank_id=SHARED_BANK,
        text="Abstract expressionism emphasizes spontaneous, automatic, or subconscious creation.",
        topic="art",
        tags=["art", "painting", "abstract"],
        strength=0.60,
        novelty=0.5,
        surprise=0.4,
        task_relevance=0.35,
        emotional_valence=0.5,
    ),
    EngramFixture(
        id=_uid("art-04"),
        bank_id=SHARED_BANK,
        text="Jazz music emerged from African American communities in New Orleans around 1900.",
        topic="art",
        tags=["art", "music", "jazz"],
        strength=0.55,
        novelty=0.5,
        surprise=0.4,
        task_relevance=0.30,
        emotional_valence=0.6,
    ),
    EngramFixture(
        id=_uid("art-05"),
        bank_id=SHARED_BANK,
        text="The Bauhaus school unified fine art, craft, and industrial design principles.",
        topic="art",
        tags=["art", "design", "architecture"],
        strength=0.50,
        novelty=0.4,
        surprise=0.3,
        task_relevance=0.35,
        emotional_valence=0.2,
    ),
]

# Populate deterministic embeddings
for _idx, _e in enumerate(ENGRAMS):
    _member_idx = _idx % 5
    _e.embedding = cluster_embedding(_e.topic, _member_idx)

# ---------------------------------------------------------------------------
# Ground truth: expected engram IDs per topic query
# ---------------------------------------------------------------------------
GROUND_TRUTH: dict[str, list[str]] = {
    topic: [e.id for e in ENGRAMS if e.topic == topic] for topic in ["tech", "science", "history", "art"]
}

# Engrams by bank for dual-bank tests
AGENT_ENGRAM_IDS = {e.id for e in ENGRAMS if e.bank_id == AGENT_BANK}
SHARED_ENGRAM_IDS = {e.id for e in ENGRAMS if e.bank_id == SHARED_BANK}

# Neo4j SIMILAR_TO links: same-topic engrams are linked
NEO4J_LINKS: list[tuple[str, str, str]] = []  # (from_id, rel_type, to_id)
for _topic in ["tech", "science", "history", "art"]:
    _cluster = [e for e in ENGRAMS if e.topic == _topic]
    for _i in range(len(_cluster) - 1):
        NEO4J_LINKS.append((_cluster[_i].id, "SIMILAR_TO", _cluster[_i + 1].id))


# ---------------------------------------------------------------------------
# Insertion helpers (called from conftest fixtures)
# ---------------------------------------------------------------------------


async def insert_into_postgres(pool, bank_id: str) -> None:
    """Insert memory_units + engram_dictionary rows for fixtures in the given bank."""
    engrams_for_bank = [e for e in ENGRAMS if e.bank_id == bank_id]
    async with pool.acquire() as conn:
        for e in engrams_for_bank:
            await conn.execute(
                """
                INSERT INTO memory_units (id, bank_id, text, fact_type)
                VALUES ($1::uuid, $2, $3, 'general')
                ON CONFLICT (id) DO NOTHING
                """,
                e.id,
                bank_id,
                e.text,
            )
            await conn.execute(
                """
                INSERT INTO engram_dictionary
                    (engram_id, bank_id, strength, novelty, surprise, task_relevance, emotional_valence)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (engram_id) DO NOTHING
                """,
                e.id,
                bank_id,
                e.strength,
                e.novelty,
                e.surprise,
                e.task_relevance,
                e.emotional_valence,
            )


async def insert_into_qdrant(qdrant_client, bank_id: str) -> None:
    """Insert vector points for fixtures in the given bank into Qdrant."""
    engrams_for_bank = [e for e in ENGRAMS if e.bank_id == bank_id]
    points = [
        {
            "engram_id": e.id,
            "vector": e.embedding,
            "payload": {
                "bank_id": bank_id,
                "tags": e.tags,
                "topic": e.topic,
            },
        }
        for e in engrams_for_bank
    ]
    await qdrant_client.batch_upsert(points)


async def insert_into_neo4j(neo4j_client) -> None:
    """Insert Engram nodes + SIMILAR_TO relationships into Neo4j."""
    for e in ENGRAMS:
        await neo4j_client.create_node(
            label="Engram",
            properties={
                "engram_id": e.id,
                "bank_id": e.bank_id,
                "topic": e.topic,
                "strength": e.strength,
            },
        )
    for from_id, rel_type, to_id in NEO4J_LINKS:
        try:
            await neo4j_client.create_relationship(
                from_engram_id=from_id,
                to_engram_id=to_id,
                rel_type=rel_type,
                properties={"weight": 0.8},
            )
        except Exception:
            pass  # Ignore duplicate relationship errors


async def cleanup_test_data(pool, qdrant_client, neo4j_client) -> None:
    """Remove all test fixture data from all three stores."""
    test_ids = [e.id for e in ENGRAMS]
    # PostgreSQL cleanup
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM engram_dictionary WHERE engram_id::text = ANY($1)", test_ids)
        await conn.execute("DELETE FROM memory_units WHERE id::text = ANY($1)", test_ids)
    # Qdrant cleanup
    for eid in test_ids:
        try:
            await qdrant_client.delete_by_id(eid)
        except Exception:
            pass
    # Neo4j cleanup
    for eid in test_ids:
        try:
            await neo4j_client.delete_node(eid, cascade_relationships=True)
        except Exception:
            pass
