import json
import logging

import redis.asyncio as redis
from meridian_events import Topics

from app.metrics import forwarded_total

logger = logging.getLogger(__name__)

# Imports Topics rather than spelling out topic strings a second time —
# hardcoding {"alerts.raised": "alert", ...} directly would be a
# duplication that could silently drift from libs/events if a topic
# name ever changed there without this dict being updated too.
TOPIC_TO_NOTIFICATION_TYPE = {
    Topics.ALERTS_RAISED: "alert",
    Topics.INSIGHTS_GENERATED: "insight",
}


def build_notification(topic: str, payload: bytes) -> tuple[str, dict] | None:
    """Returns (user_id, notification_envelope) for a recognized topic,
    or None if the topic isn't one this service knows how to fan out."""
    notification_type = TOPIC_TO_NOTIFICATION_TYPE.get(topic)
    if notification_type is None:
        return None

    data = json.loads(payload)
    user_id = data.get("user_id")
    if user_id is None:
        return None

    return user_id, {"type": notification_type, "data": data}


async def process_message(topic: str, payload: bytes, redis_client: redis.Redis) -> bool:
    """Returns True if a notification was actually published — false for
    a message on a topic this service doesn't recognize (logged, not an
    error; a genuinely unexpected case given the consumer only subscribes
    to the two topics it knows about)."""
    built = build_notification(topic, payload)
    if built is None:
        logger.warning("Received a message on unrecognized topic %s; skipping.", topic)
        return False

    user_id, notification = built
    # Redis Pub/Sub has no persistence — a notification published while
    # no one is subscribed (dashboard closed) is simply lost. Acceptable
    # for a live-update nicety; not the system of record (GET /alerts is).
    await redis_client.publish(f"notifications:{user_id}", json.dumps(notification))
    forwarded_total.labels(type=notification["type"]).inc()
    return True
