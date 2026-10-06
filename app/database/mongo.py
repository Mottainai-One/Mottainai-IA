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
        _client = AsyncIOMotorClient(
            settings.mongo_uri,
            serverSelectionTimeoutMS=int(settings.mongo_server_selection_timeout_seconds * 1000),
            connectTimeoutMS=int(settings.mongo_connect_timeout_seconds * 1000),
            socketTimeoutMS=int(settings.mongo_socket_timeout_seconds * 1000),
        )
    return _client


def get_mongo_db() -> AsyncIOMotorDatabase:
    return get_mongo_client()[settings.mongo_db]


async def close_mongo_client() -> None:
    """Closes the process-wide MongoDB client during shutdown."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
