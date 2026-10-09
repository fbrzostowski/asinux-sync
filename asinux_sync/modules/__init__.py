from .base import SyncModule
from .background import BackgroundModule

REGISTRY: dict[str, type[SyncModule]] = {
    BackgroundModule.module_id: BackgroundModule,
}

__all__ = ["SyncModule", "BackgroundModule", "REGISTRY"]
