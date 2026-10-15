"""Narration cache/audit log: Azure Cosmos DB when configured, else in-memory."""
import hashlib
import os

_mem = {}
_container = None


def _cosmos():
    global _container
    if _container is None and os.getenv("COSMOS_ENDPOINT"):
        from azure.cosmos import CosmosClient
        from azure.identity import DefaultAzureCredential
        cred = os.getenv("COSMOS_KEY") or DefaultAzureCredential()
        db = CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=cred).get_database_client("pitchside")
        _container = db.get_container_client("narrations")
    return _container


def key(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()


def get(k: str):
    if k in _mem:
        return _mem[k]
    try:
        c = _cosmos()
        if c:
            doc = c.read_item(k, partition_key=k)
            _mem[k] = doc["payload"]
            return _mem[k]
    except Exception:
        pass
    return None


def put(k: str, payload: dict):
    _mem[k] = payload
    try:
        c = _cosmos()
        if c:
            c.upsert_item({"id": k, "payload": payload})
    except Exception:
        pass
