"""
Async connection to MongoDB (motor).
AI-only layer: conversations, memory, RAG, governance, metrics.
"""
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import get_settings

settings = get_settings()

_client: AsyncIOMotorClient | None = None


def get_mongo_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(settings.mongo_uri)
    return _client


def get_mongo_db() -> AsyncIOMotorDatabase:
    return get_mongo_client()[settings.mongo_db]
