from .appearance import AppearanceModule
from .background import BackgroundModule
from .base import Item, ModuleUnavailable, SyncModule
from .default_apps import DefaultAppsModule
from .dock import DockModule
from .extensions import ExtensionsModule
from .layout import LayoutModule
from .multitasking import MultitaskingModule
from .notifications import NotificationsModule
from .power import PowerModule
from .profile_picture import ProfilePictureModule
from .shortcuts import ShortcutsModule
from .window import WindowModule

# Order matters: it is the order modules are applied in (extensions before
# their settings), and the order the panel lists them in.
REGISTRY: dict[str, type[SyncModule]] = {
    cls.module_id: cls
    for cls in (
        BackgroundModule,
        AppearanceModule,
        WindowModule,
        MultitaskingModule,
        DockModule,
        ExtensionsModule,
        LayoutModule,
        ShortcutsModule,
        DefaultAppsModule,
        NotificationsModule,
        PowerModule,
        ProfilePictureModule,
    )
}

__all__ = ["SyncModule", "Item", "ModuleUnavailable", "REGISTRY"]
