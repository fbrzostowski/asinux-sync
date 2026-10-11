from .appearance import AppearanceModule
from .background import BackgroundModule
from .base import ModuleUnavailable, SyncModule
from .default_apps import DefaultAppsModule
from .dock import DockModule
from .layout import LayoutModule
from .profile_picture import ProfilePictureModule
from .shortcuts import ShortcutsModule
from .window import WindowModule

# Order matters: it is the order modules are applied in, and the order the
# panel lists them in.
REGISTRY: dict[str, type[SyncModule]] = {
    cls.module_id: cls
    for cls in (
        BackgroundModule,
        AppearanceModule,
        WindowModule,
        DockModule,
        LayoutModule,
        ShortcutsModule,
        DefaultAppsModule,
        ProfilePictureModule,
    )
}

__all__ = ["SyncModule", "ModuleUnavailable", "REGISTRY"]
