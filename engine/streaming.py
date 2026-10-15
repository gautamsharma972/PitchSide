"""Publish match events to Azure Event Hubs (feeds a Fabric Eventstream -> Eventhouse in real time)."""
import json
import os

_producer = None


def enabled() -> bool:
    return bool(os.getenv("EVENTHUB_NAMESPACE") and os.getenv("EVENTHUB_NAME"))


def _get():
    global _producer
    if _producer is None:
        from azure.eventhub import EventHubProducerClient
        from azure.identity import DefaultAzureCredential
        _producer = EventHubProducerClient(fully_qualified_namespace=os.environ["EVENTHUB_NAMESPACE"],
                                           eventhub_name=os.environ["EVENTHUB_NAME"],
                                           credential=DefaultAzureCredential())
    return _producer


def publish(event: dict, seed: int) -> bool:
    """Best-effort: a streaming failure must never break the live feed."""
    if not enabled():
        return False
    try:
        from azure.eventhub import EventData
        p = _get()
        batch = p.create_batch(partition_key=str(seed))
        keys = ("id", "elapsed", "clock", "minute", "team", "poss", "player", "to_player", "type", "x", "y", "to_x", "to_y",
                "success", "distance_m", "difficulty", "ball_speed", "shot_speed", "player_speed", "pressure", "xg", "detail")
        doc = {"match": seed, **{k: event.get(k) for k in keys}, "state": event.get("state")}
        batch.add(EventData(json.dumps(doc)))
        p.send_batch(batch)
        return True
    except Exception:
        return False
