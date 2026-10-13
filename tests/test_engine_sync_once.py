import json
import shutil
from pathlib import Path

from asinux_sync.engine import PROFILE_VERSION, SyncEngine
from asinux_sync.modules.base import Item, ModuleUnavailable, SyncModule


class DirBackend:
    """Stands in for rclone: the 'cloud' is a local directory."""

    def __init__(self, root: Path):
        self.root = root

    def _remote(self, remote_path):
        return self.root / remote_path

    def exists_dir(self, remote_path):
        return self._remote(remote_path).is_dir()

    def sync(self, local_dir, remote_path, *, direction="push"):
        src, dst = (Path(local_dir), self._remote(remote_path)) if direction == "push" else (self._remote(remote_path), Path(local_dir))
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)


class FakeModule(SyncModule):
    """Items are 'a' and 'b'; the store maps '<module>.<item>' to a value."""

    def __init__(self, module_id, store, item_ids=("a", "b")):
        self.module_id = module_id
        self.store = store
        self.item_ids = item_ids

    def list_items(self):
        return [Item(i, i) for i in self.item_ids]

    def capture_item(self, item_id, assets_dir):
        key = f"{self.module_id}.{item_id}"
        return {"value": self.store[key]} if key in self.store else None

    def apply_item(self, item_id, data, assets_dir):
        self.store[f"{self.module_id}.{item_id}"] = data["value"]


def make_device(tmp_path, name, cloud, modules=("background", "theme")):
    store = {}
    engine = SyncEngine(DirBackend(cloud), {m: FakeModule(m, store) for m in modules}, tmp_path / name)
    return engine, store


def cloud_profile(cloud):
    return json.loads((cloud / "asinux-sync/profile/profile.json").read_text())


def test_first_device_pushes_then_idles(tmp_path):
    a, store = make_device(tmp_path, "a", tmp_path / "cloud")
    store.update({"background.a": "wall1", "theme.a": "dark"})
    assert a.sync_once() == "pushed"
    assert a.sync_once() == "idle"


def test_local_change_pushes_and_other_device_pulls(tmp_path):
    cloud = tmp_path / "cloud"
    a, store_a = make_device(tmp_path, "a", cloud)
    b, store_b = make_device(tmp_path, "b", cloud)
    store_a.update({"background.a": "wall1", "theme.a": "dark"})
    a.sync_once()

    assert b.sync_once() == "pulled"  # fresh device: cloud wins
    assert store_b == {"background.a": "wall1", "theme.a": "dark"}
    assert b.sync_once() == "idle"

    store_a["background.a"] = "wall2"
    assert a.sync_once() == "pushed"
    assert b.sync_once() == "pulled"
    assert store_b["background.a"] == "wall2"
    assert a.sync_once() == "idle"


def test_fresh_device_never_overwrites_cloud(tmp_path):
    cloud = tmp_path / "cloud"
    a, store_a = make_device(tmp_path, "a", cloud)
    store_a.update({"background.a": "wall1"})
    a.sync_once()

    b, store_b = make_device(tmp_path, "b", cloud)
    store_b.update({"background.a": "other"})
    b.sync_once()
    assert store_b["background.a"] == "wall1"


def test_disabled_module_is_not_applied_and_survives_pushes(tmp_path):
    cloud = tmp_path / "cloud"
    a, store_a = make_device(tmp_path, "a", cloud)
    b, store_b = make_device(tmp_path, "b", cloud)
    store_a.update({"background.a": "wall1", "theme.a": "dark"})
    a.sync_once()

    store_b["background.a"] = "my-own"
    b.sync_once({"background"})
    assert store_b["background.a"] == "my-own"
    assert store_b["theme.a"] == "dark"

    store_b["theme.a"] = "light"
    assert b.sync_once({"background"}) == "pushed"
    assert cloud_profile(cloud)["modules"]["background"]["items"]["a"] == {"value": "wall1"}


def test_single_item_can_be_switched_off(tmp_path):
    cloud = tmp_path / "cloud"
    a, store_a = make_device(tmp_path, "a", cloud)
    b, store_b = make_device(tmp_path, "b", cloud)
    store_a.update({"theme.a": "dark", "theme.b": "big"})
    a.sync_once()

    store_b.update({"theme.a": "mine"})
    b.sync_once({"theme.a"})  # only item `a` of `theme` is off on B
    assert store_b == {"theme.a": "mine", "theme.b": "big"}

    store_b["theme.b"] = "small"
    store_b["theme.a"] = "mine-changed"  # changes to the switched-off item don't count
    assert b.sync_once({"theme.a"}) == "pushed"
    items = cloud_profile(cloud)["modules"]["theme"]["items"]
    assert items == {"a": {"value": "dark"}, "b": {"value": "small"}}

    assert a.sync_once() == "pulled"
    assert store_a["theme.a"] == "dark" and store_a["theme.b"] == "small"


def test_items_this_device_cannot_capture_stay_in_the_cloud(tmp_path):
    cloud = tmp_path / "cloud"
    a, store_a = make_device(tmp_path, "a", cloud)
    store_a.update({"theme.a": "x", "theme.b": "y"})
    a.sync_once()

    # Device B has no `b` item at all (e.g. that extension isn't installed there).
    store_b = {"theme.a": "x"}
    b = SyncEngine(DirBackend(cloud), {"theme": FakeModule("theme", store_b, item_ids=("a",))}, tmp_path / "b")
    b.sync_once()
    store_b["theme.a"] = "changed"
    assert b.sync_once() == "pushed"
    assert cloud_profile(cloud)["modules"]["theme"]["items"]["b"] == {"value": "y"}


def test_failing_modules_are_skipped_and_do_not_wipe_the_cloud_copy(tmp_path):
    class Broken(FakeModule):
        def __init__(self, module_id, error):
            super().__init__(module_id, {})
            self.error = error

        def capture_item(self, item_id, assets_dir):
            raise self.error

    cloud = tmp_path / "cloud"
    healthy, store = make_device(tmp_path, "a", cloud)
    store.update({"background.a": "wall1", "theme.a": "dark"})
    healthy.sync_once()

    store_b = {}
    broken = SyncEngine(
        DirBackend(cloud),
        {
            "background": FakeModule("background", store_b),
            "theme": Broken("theme", RuntimeError("boom")),
            "layout": Broken("layout", ModuleUnavailable("not installed")),
        },
        tmp_path / "b",
    )
    assert broken.sync_once() == "pulled"  # fresh device: no crash
    store_b["background.a"] = "mine"
    assert broken.sync_once() == "pushed"

    modules = cloud_profile(cloud)["modules"]
    assert modules["theme"]["items"]["a"] == {"value": "dark"}  # the broken module did not erase it
    assert modules["background"]["items"]["a"] == {"value": "mine"}


def test_modules_apply_in_registry_order(tmp_path):
    order = []

    class Recorder(FakeModule):
        def apply_item(self, item_id, data, assets_dir):
            order.append(self.module_id)

    cloud = tmp_path / "cloud"
    a, store = make_device(tmp_path, "a", cloud)
    store.update({"background.a": "x", "theme.a": "y"})
    a.sync_once()
    b = SyncEngine(DirBackend(cloud), {"theme": Recorder("theme", {}), "background": Recorder("background", {})}, tmp_path / "b")
    b.sync_once()
    assert order == ["theme", "background"]  # the engine's module order, not the profile's


def _write_old_profile(cloud):
    folder = cloud / "asinux-sync/profile"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "profile.json").write_text(json.dumps({
        "version": 1, "updated_at": "2026-01-01T00:00:00+00:00", "revision": "old", "updated_by_device": "pc1",
        "modules": {"background": {"background": {"picture-uri": "x"}}},
    }))


def test_fresh_device_leaves_an_old_format_profile_alone(tmp_path):
    cloud = tmp_path / "cloud"
    _write_old_profile(cloud)
    b, store_b = make_device(tmp_path, "b", cloud)
    store_b["background.a"] = "mine"
    assert b.sync_once() == "idle"
    assert cloud_profile(cloud)["version"] == 1  # untouched


def test_synced_device_migrates_an_old_format_profile(tmp_path):
    cloud = tmp_path / "cloud"
    a, store_a = make_device(tmp_path, "a", cloud)
    store_a["background.a"] = "wall"
    a.sync_once()
    _write_old_profile(cloud)  # the cloud got overwritten by an older client
    assert a.sync_once() == "pushed"
    assert cloud_profile(cloud)["version"] == PROFILE_VERSION


def test_manual_pull_ignores_old_format(tmp_path):
    cloud = tmp_path / "cloud"
    _write_old_profile(cloud)
    b, _ = make_device(tmp_path, "b", cloud)
    assert b.pull() is None


def test_collection_items_keep_entries_this_device_does_not_have(tmp_path):
    class Collection(FakeModule):
        def capture_item(self, item_id, assets_dir):
            return {"settings": dict(self.store)} if self.store else None

        def apply_item(self, item_id, data, assets_dir):
            self.store.update(data["settings"])

        def merge_item(self, item_id, remote, local):
            return {"settings": {**remote["settings"], **local["settings"]}}

    cloud = tmp_path / "cloud"
    store_a, store_b = {"ext1": "a1", "ext2": "a2"}, {}
    a = SyncEngine(DirBackend(cloud), {"layout": Collection("layout", store_a, ("all",))}, tmp_path / "a")
    b = SyncEngine(DirBackend(cloud), {"layout": Collection("layout", store_b, ("all",))}, tmp_path / "b")
    a.sync_once()
    b.sync_once()
    store_b["ext1"] = "b1"  # B has only ext1 (ext2 isn't installed there)
    del store_b["ext2"]
    assert b.sync_once() == "pushed"
    assert cloud_profile(cloud)["modules"]["layout"]["items"]["all"]["settings"] == {"ext1": "b1", "ext2": "a2"}


def _synced_pair(tmp_path):
    cloud = tmp_path / "cloud"
    a, store_a = make_device(tmp_path, "a", cloud)
    b, store_b = make_device(tmp_path, "b", cloud)
    store_a.update({"background.a": "wall1", "theme.a": "dark"})
    a.sync_once()
    b.sync_once()
    assert a.sync_once() == "idle" and b.sync_once() == "idle"
    return cloud, (a, store_a), (b, store_b)


def test_a_change_made_here_is_uploaded_not_overwritten_by_the_cloud(tmp_path):
    cloud, (a, store_a), (b, store_b) = _synced_pair(tmp_path)

    store_b["background.a"] = "wall-from-b"
    assert b.sync_once() == "pushed"  # the cloud now holds B's wallpaper
    store_a["background.a"] = "my-new-wall"  # A changes it before it has seen B's change

    assert a.sync_once() == "pushed"  # A's edit wins and is uploaded...
    assert store_a["background.a"] == "my-new-wall"  # ...instead of B's value being downloaded over it
    assert cloud_profile(cloud)["modules"]["background"]["items"]["a"] == {"value": "my-new-wall"}
    assert b.sync_once() == "pulled" and store_b["background.a"] == "my-new-wall"


def test_changes_to_different_settings_are_all_kept(tmp_path):
    cloud, (a, store_a), (b, store_b) = _synced_pair(tmp_path)

    store_a["background.a"] = "wall-from-a"
    store_b["theme.a"] = "light-from-b"
    assert b.sync_once() == "pushed"  # B uploads its theme change first
    assert a.sync_once() == "pushed"  # A uploads its wallpaper AND takes B's theme
    assert store_a == {"background.a": "wall-from-a", "theme.a": "light-from-b"}
    assert b.sync_once() == "pulled"
    assert store_b == {"background.a": "wall-from-a", "theme.a": "light-from-b"}
    assert a.sync_once() == "idle" and b.sync_once() == "idle"
    items = {m: v["items"] for m, v in cloud_profile(cloud)["modules"].items()}
    assert items == {"background": {"a": {"value": "wall-from-a"}}, "theme": {"a": {"value": "light-from-b"}}}


def test_switching_a_category_on_takes_the_cloud_values_when_it_has_them(tmp_path):
    cloud = tmp_path / "cloud"
    a, store_a = make_device(tmp_path, "a", cloud)
    b, store_b = make_device(tmp_path, "b", cloud)
    store_a.update({"theme.a": "cloud-value"})
    a.sync_once()

    store_b["theme.a"] = "my-own"
    b.sync_once({"theme"})  # theme switched off on B: nothing happens to it
    assert store_b["theme.a"] == "my-own"

    assert b.sync_once() == "pulled"  # switched on later: the cloud's value wins, B's is not uploaded
    assert store_b["theme.a"] == "cloud-value"
    assert cloud_profile(cloud)["modules"]["theme"]["items"]["a"] == {"value": "cloud-value"}


def test_switching_a_category_on_uploads_it_when_the_cloud_has_nothing(tmp_path):
    cloud = tmp_path / "cloud"
    a, store_a = make_device(tmp_path, "a", cloud)
    store_a.update({"background.a": "wall"})
    a.sync_once({"theme"})
    store_a["theme.a"] = "dark"
    assert a.sync_once() == "pushed"
    assert cloud_profile(cloud)["modules"]["theme"]["items"]["a"] == {"value": "dark"}


def test_only_the_item_that_changed_is_uploaded(tmp_path):
    cloud, (a, store_a), (b, store_b) = _synced_pair(tmp_path)
    before = cloud_profile(cloud)
    store_a["background.a"] = "new"
    a.sync_once()
    after = cloud_profile(cloud)
    assert after["modules"]["theme"] == before["modules"]["theme"]
    assert after["revision"] != before["revision"]


def test_cloud_has_item(tmp_path):
    cloud = tmp_path / "cloud"
    a, store = make_device(tmp_path, "a", cloud)
    assert a.cloud_has_item("background.a") is False  # nothing in the cloud at all
    store["background.a"] = "wall"
    a.sync_once()
    assert a.cloud_has_item("background.a") is True
    assert a.cloud_has_item("theme.a") is False


# -- a freshly installed device must not push its defaults ------------------------------------------

class DefaultAware(FakeModule):
    """Like the GSettings modules: always reports data, but knows when it is all defaults."""

    def capture_item(self, item_id, assets_dir):
        return {"value": self.store.get(f"{self.module_id}.{item_id}")}

    def is_default(self, item_id, data):
        return data["value"] is None


def test_a_fresh_device_does_not_upload_its_defaults_for_items_the_cloud_lacks(tmp_path):
    cloud = tmp_path / "cloud"
    a, store_a = make_device(tmp_path, "a", cloud)
    store_a["background.a"] = "wall1"
    a.sync_once()  # the cloud has the wallpaper, nothing about the theme yet

    b, store_b = make_device(tmp_path, "b", cloud)
    store_b["theme.a"] = "installer-default"  # what a fresh install happens to have
    b.sync_once()  # B's first sync
    assert "theme" not in cloud_profile(cloud)["modules"]  # its defaults did not become "the profile"

    store_a["theme.a"] = "dark"  # A, who actually customised the theme, syncs it for the first time
    assert a.sync_once() == "pushed"
    assert store_a["theme.a"] == "dark"  # not replaced by B's installer value
    assert b.sync_once() == "pulled" and store_b["theme.a"] == "dark"  # B takes what the cloud has


def test_what_a_fresh_device_changes_after_joining_is_uploaded(tmp_path):
    cloud = tmp_path / "cloud"
    a, store_a = make_device(tmp_path, "a", cloud)
    store_a["background.a"] = "wall1"
    a.sync_once()
    b, store_b = make_device(tmp_path, "b", cloud)
    store_b["theme.a"] = "installer-default"
    b.sync_once()

    store_b["theme.a"] = "my-choice"
    assert b.sync_once() == "pushed"
    assert cloud_profile(cloud)["modules"]["theme"]["items"]["a"] == {"value": "my-choice"}


def test_an_established_device_does_not_upload_default_values_of_a_new_module(tmp_path):
    cloud = tmp_path / "cloud"
    modules = lambda store: {"background": FakeModule("background", store), "extra": DefaultAware("extra", store)}  # noqa: E731
    store_d, store_e = {"background.a": "wall"}, {"background.a": "wall"}
    d = SyncEngine(DirBackend(cloud), modules(store_d), tmp_path / "d")
    e = SyncEngine(DirBackend(cloud), modules(store_e), tmp_path / "e")
    d.sync_once()  # first device: uploads what it has; `extra` is all defaults, so it is left out
    assert "extra" not in cloud_profile(cloud)["modules"]
    e.sync_once()
    store_e["extra.a"] = "customised"
    assert e.sync_once() == "pushed"
    assert d.sync_once() == "pulled" and store_d["extra.a"] == "customised"
