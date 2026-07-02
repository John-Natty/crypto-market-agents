"""Optional notification integrations."""

from crypto_market_agents.notifications.whatsapp_client import (
    NotificationResult,
    WhatsAppClient,
)
from crypto_market_agents.notifications.whatsapp_notifier import WhatsAppNotifier

__all__ = [
    "NotificationResult",
    "WhatsAppClient",
    "WhatsAppNotifier",
]
