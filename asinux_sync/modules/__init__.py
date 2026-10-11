from .accessibility import AccessibilityModule
from .app_grid import AppGridModule
from .appearance import AppearanceModule
from .background import BackgroundModule
from .base import Item, ModuleUnavailable, SyncModule
from .datetime_settings import DateTimeModule
from .default_apps import DefaultAppsModule
from .dock import DockModule
from .extensions import ExtensionsModule
from .fonts import FontsModule
from .keyboard import KeyboardModule
from .layout import LayoutModule
from .mouse import MouseModule
from .multitasking import MultitaskingModule
from .night_light import NightLightModule
from .notifications import NotificationsModule
from .power import PowerModule
from .profile_picture import ProfilePictureModule
from .region import RegionModule
from .search import SearchModule
from .shortcuts import ShortcutsModule
from .window import WindowModule

# Order matters: it is the order modules are applied in (extensions before
# their settings), and the order the panel lists them in.
REGISTRY: dict[str, type[SyncModule]] = {
    cls.module_id: cls
    for cls in (
        BackgroundModule,
        AppearanceModule,
        FontsModule,
        WindowModule,
        MultitaskingModule,
        DockModule,
        AppGridModule,
        ExtensionsModule,
        LayoutModule,
        KeyboardModule,
        ShortcutsModule,
        MouseModule,
        DefaultAppsModule,
        NotificationsModule,
        SearchModule,
        RegionModule,
        DateTimeModule,
        PowerModule,
        NightLightModule,
        AccessibilityModule,
        ProfilePictureModule,
    )
}

__all__ = ["SyncModule", "Item", "ModuleUnavailable", "REGISTRY"]
