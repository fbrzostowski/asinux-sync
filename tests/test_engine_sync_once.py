import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asinux_sync.engine import SyncEngine  # noqa: E402
from asinux_sync.modules.base import SyncModule  # noqa: E402


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
    def __init__(self, module_id, store):
        self.module_id = module_id
        self.store = store

    def capture(self, assets_dir):
        return {"value": self.store.get(self.module_id, "")}

    def apply(self, data, assets_dir):
        self.store[self.module_id] = data["value"]


def make_device(tmp_path, name, cloud):
    store = {}
    modules = {m: FakeModule(m, store) for m in ("background", "theme")}
    engine = SyncEngine(DirBackend(cloud), modules, tmp_path / name)
    return engine, store


ALL = ["background", "theme"]


def test_first_device_pushes_then_idles(tmp_path):
    a, store = make_device(tmp_path, "a", tmp_path / "cloud")
    store.update(background="wall1", theme="dark")
    assert a.sync_once(ALL) == "pushed"
    assert a.sync_once(ALL) == "idle"


def test_local_change_pushes_and_other_device_pulls(tmp_path):
    cloud = tmp_path / "cloud"
    a, store_a = make_device(tmp_path, "a", cloud)
    b, store_b = make_device(tmp_path, "b", cloud)
    store_a.update(background="wall1", theme="dark")
    a.sync_once(ALL)

    assert b.sync_once(ALL) == "pulled"  # fresh device: cloud wins
    assert store_b == {"background": "wall1", "theme": "dark"}
    assert b.sync_once(ALL) == "idle"

    store_a["background"] = "wall2"
    assert a.sync_once(ALL) == "pushed"
    assert b.sync_once(ALL) == "pulled"
    assert store_b["background"] == "wall2"
    assert a.sync_once(ALL) == "idle"


def test_fresh_device_never_overwrites_cloud(tmp_path):
    cloud = tmp_path / "cloud"
    a, store_a = make_device(tmp_path, "a", cloud)
    store_a.update(background="wall1", theme="dark")
    a.sync_once(ALL)

    b, store_b = make_device(tmp_path, "b", cloud)
    store_b.update(background="other", theme="light")
    b.sync_once(ALL)
    assert store_b == {"background": "wall1", "theme": "dark"}


def test_disabled_module_is_not_applied_and_survives_pushes(tmp_path):
    cloud = tmp_path / "cloud"
    a, store_a = make_device(tmp_path, "a", cloud)
    b, store_b = make_device(tmp_path, "b", cloud)
    store_a.update(background="wall1", theme="dark")
    a.sync_once(ALL)

    store_b["background"] = "my-own"
    b.sync_once(["theme"])  # background disabled on B
    assert store_b["background"] == "my-own"
    assert store_b["theme"] == "dark"

    store_b["theme"] = "light"
    assert b.sync_once(["theme"]) == "pushed"
    store_a["theme"] = "dark"  # unrelated: A still reads the shared profile
    a_store_before = dict(store_a)
    a.sync_once(ALL)
    # B's push must not have wiped A's background from the shared profile.
    profile_modules = __import__("json").loads((cloud / "asinux-sync/profile/profile.json").read_text())["modules"]
    assert profile_modules["background"] == {"value": "wall1"}
    assert a_store_before["background"] == "wall1"
