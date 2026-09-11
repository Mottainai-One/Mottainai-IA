"""Keyword-based routing data for the supervisor, backed by the
intent_catalog collection instead of hardcoded Python sets.

Before this, node_supervisor_route carried two literal keyword sets
(KEYWORDS_PREDITIVO, FAQ_KEYWORDS) that only a code change and a redeploy
could touch. The matching rule itself is unchanged on purpose — this moves
where the keywords live, not how routing decides: a DONO/CLIENTE message
still routes on "does the lowercased text contain one of this intent's
examples", the same substring check as before. A real routing model is a
different, untested project; this only gets the data out of source control.

Role-based access (which agents a role may even reach) stays hardcoded in
supervisor.py's ROLE_TO_INTENT_AGENTS — that is an authorization boundary,
and editing a Mongo document must never be able to route a CLIENTE to
`dono`.

FALLBACK_INTENTS is the single source of truth for both this module's
in-process fallback and scripts/setup_mongo.py's seed, so the two can't
drift apart.
"""
import asyncio
import logging
import time

from app.database.mongo import get_mongo_db

logger = logging.getLogger(__name__)

# Below settings.rag_cache_ttl_seconds (300s): a routing edit made for QA
# should surface quickly, not sit behind a five-minute cache.
CACHE_TTL_SECONDS = 60

# Bounds one cold-cache Mongo round trip. Nothing in this codebase sets
# serverSelectionTimeoutMS on the shared Mongo client, so an unreachable
# Mongo would otherwise cost pymongo's default server-selection wait
# (~30s) per attempt instead of failing fast into the fallback below.
LOOKUP_TIMEOUT_S = 1.5

FALLBACK_INTENTS: list[dict] = [
    {
        "key": "motor_preditivo_forecast",
        "agent": "motor_preditivo",
        "label": "Previsão de demanda",
        "active": True,
        "confidenceThreshold": 0.0,
        "examples": [
            "previsão", "previsao", "prever", "preve", "prevê",
            "demanda", "abastecimento", "tendência", "tendencia",
            "vai acabar", "quando acaba", "risco de falta", "risco de perda",
            "projeção", "projecao", "próxima semana", "proxima semana",
            "próximo mês", "proximo mes",
        ],
    },
    {
        "key": "cliente_faq",
        "agent": "faq",
        "label": "Dúvidas gerais (FAQ)",
        "active": True,
        "confidenceThreshold": 0.0,
        "examples": [
            "faq", "dúvida", "duvida", "como funciona", "ajuda",
            "suporte", "fidelidade", "pontos", "sustentabilidade",
        ],
    },
]

_cache: list[dict] | None = None
_cache_fetched_monotonic: float | None = None


async def _fetch_active_intents() -> list[dict]:
    db = get_mongo_db()
    return [doc async for doc in db.intent_catalog.find({"active": True})]


async def get_active_intents() -> list[dict]:
    """
    TTL-cached read of active intent_catalog documents.

    Fail-open to FALLBACK_INTENTS on a cold-cache timeout, error, or empty
    result — routing must never block on, or break because of, a Mongo
    hiccup. The TTL clock is stamped on every attempt, success or failure,
    so an outage costs one LOOKUP_TIMEOUT_S stall per CACHE_TTL_SECONDS
    window for the whole process, not one per request.
    """
    global _cache, _cache_fetched_monotonic
    now = time.monotonic()
    if _cache is not None and _cache_fetched_monotonic is not None:
        if now - _cache_fetched_monotonic < CACHE_TTL_SECONDS:
            return _cache

    _cache_fetched_monotonic = now
    try:
        intents = await asyncio.wait_for(_fetch_active_intents(), timeout=LOOKUP_TIMEOUT_S)
    except Exception:
        logger.warning("intent_catalog unavailable — falling back to built-in keywords", exc_info=True)
        _cache = FALLBACK_INTENTS
        return _cache

    _cache = intents or FALLBACK_INTENTS
    return _cache


def match_intent(
    text: str, candidate_agents: list[str], intents: list[dict],
) -> tuple[str | None, str | None, float | None]:
    """
    Returns (intent_key, agent, confidence) for the highest-confidence
    active intent among `intents` whose agent is in `candidate_agents` and
    whose examples substring-match `text`, or (None, None, None) if
    nothing clears its own confidenceThreshold.

    confidence is the fraction of an intent's examples found in `text`.
    With confidenceThreshold=0.0 (today's seed) a single matching keyword
    is already enough to route — exactly the `any(kw in text for kw in
    KEYWORDS)` this replaces.
    """
    best: tuple[str, str, float] | None = None
    for intent in intents:
        if intent.get("agent") not in candidate_agents:
            continue
        examples = intent.get("examples") or []
        if not examples:
            continue
        matches = sum(1 for example in examples if example in text)
        if matches == 0:
            continue
        confidence = matches / len(examples)
        if confidence < intent.get("confidenceThreshold", 0.0):
            continue
        if best is None or confidence > best[2]:
            best = (intent["key"], intent["agent"], confidence)

    return best if best is not None else (None, None, None)
