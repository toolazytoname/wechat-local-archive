"""Process data roots, loopback port selection, and first-run paths."""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path

from wechat_export.loopback import LOOPBACK_HOST

APP_SUPPORT_NAME = "wechat-local-archive"
CHECKOUT_MARKER = "pyproject.toml"


@dataclass(frozen=True)
class RuntimePaths:
    data_root: Path
    kind: str
    project_root: Path | None
    exports_root: Path
    jobs_root: Path
    private_root: Path
    slices_root: Path


def package_root() -> Path:
    return Path(__file__).resolve().parent


def source_checkout_root() -> Path | None:
    candidate = package_root().parent
    if (candidate / CHECKOUT_MARKER).is_file() and (candidate / "wechat_export").is_dir():
        return candidate
    return None


def application_support_root() -> Path:
    override = os.environ.get("WECHAT_EXPORT_APP_SUPPORT")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library" / "Application Support" / APP_SUPPORT_NAME


def resolve_runtime(explicit_data_root: str | Path | None = None) -> RuntimePaths:
    """Prefer an existing checkout data/ directory; never migrate or delete it.

    Installed (non-checkout) runs use Application Support, not site-packages.
    """
    env_root = explicit_data_root or os.environ.get("WECHAT_EXPORT_DATA_ROOT")
    checkout = source_checkout_root()
    if env_root:
        data_root = Path(env_root).expanduser().resolve()
        kind = "env"
        project_root = checkout
    elif checkout and (checkout / "data").exists():
        data_root = checkout / "data"
        kind = "checkout"
        project_root = checkout
    else:
        data_root = application_support_root()
        kind = "application_support"
        project_root = checkout
    data_root.mkdir(parents=True, exist_ok=True)
    private = data_root / "private"
    private.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(private, 0o700)
    except OSError:
        pass
    exports = data_root / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    jobs = data_root / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    slices = data_root / "slices"
    slices.mkdir(parents=True, exist_ok=True)
    return RuntimePaths(
        data_root=data_root,
        kind=kind,
        project_root=project_root,
        exports_root=exports,
        jobs_root=jobs,
        private_root=private,
        slices_root=slices,
    )


def pick_loopback_port(preferred: int = 8765, span: int = 30) -> int:
    """Bind a free loopback port. Never terminate another process."""
    if preferred < 1 or preferred > 65535:
        raise ValueError("invalid port")
    errors: list[str] = []
    for port in range(preferred, min(preferred + span, 65536)):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            sock.bind((LOOPBACK_HOST, port))
            return port
        except OSError as exc:
            errors.append(f"{port}:{exc}")
        finally:
            sock.close()
    raise OSError(f"no free loopback port in {preferred}..{preferred + span - 1}")


def demo_export_dir() -> Path | None:
    checkout = source_checkout_root()
    if checkout:
        path = checkout / "examples" / "demo-export"
        if (path / "all" / "messages.jsonl").is_file():
            return path
    # Installed demo gets a writable runtime copy, never an index in site-packages.
    import shutil
    import tempfile
    bundled = package_root() / "demo"
    if not (bundled / "all/messages.jsonl").is_file():
        return None
    runtime = resolve_runtime()
    target = runtime.data_root / "demo-v4"
    if not target.exists():
        tmp = Path(tempfile.mkdtemp(prefix=".demo-", dir=runtime.data_root))
        try:
            shutil.copytree(bundled, tmp / "archive")
            try:
                os.rename(tmp / "archive", target)
            except OSError:
                if not target.is_dir():
                    raise
        finally:
            shutil.rmtree(tmp)
    return target


def list_local_archives(runtime: RuntimePaths) -> list[dict[str, str | int]]:
    found: list[dict[str, str | int]] = []
    demo = demo_export_dir()
    if demo:
        found.append(_archive_listing("demo", demo, "synthetic_demo"))
    if runtime.exports_root.exists():
        for child in sorted(runtime.exports_root.iterdir()):
            if not child.is_dir():
                continue
            jsonl = child / "all" / "messages.jsonl"
            if jsonl.is_file():
                found.append(_archive_listing(f"export:{child.name}", child, "local_export"))
    from wechat_export.output_locations import OutputLocations
    locations = OutputLocations(runtime)
    for destination in locations.list():
        token = destination['destination_id']
        if token == 'default' or not destination['available']:
            continue
        try:
            root = locations.resolve(token)
            for child in sorted(root.iterdir()):
                if child.is_symlink() or not child.is_dir():
                    continue
                if (child / 'all/messages.jsonl').is_file():
                    found.append(_archive_listing(locations.source_id(token, child.name), child, 'local_export'))
        except OSError:
            continue  # An unplugged volume must not hide the other archives.
    return found


def _archive_listing(source_id: str, path: Path, kind: str) -> dict[str, str | int]:
    count = 0
    manifest = path / "manifest.json"
    if manifest.is_file():
        try:
            count = int(json.loads(manifest.read_text(encoding="utf-8")).get("record_count") or 0)
        except (OSError, ValueError, json.JSONDecodeError):
            count = 0
    return {
        "source_id": source_id,
        "kind": kind,
        "name": path.name,
        "message_count": count,
        "has_index": (path / "archive.sqlite").is_file(),
    }


def resolve_source_id(source_id: str, runtime: RuntimePaths) -> Path:
    if source_id == "demo":
        demo = demo_export_dir()
        if not demo:
            raise FileNotFoundError("demo archive missing")
        return demo
    if source_id.startswith("external:"):
        from wechat_export.output_locations import OutputLocations
        parts = source_id.split(':')
        if len(parts) != 3:
            raise ValueError('invalid source_id')
        _, token, name = parts
        if not name or Path(name).name != name or name in {'.', '..'}:
            raise ValueError('invalid source_id')
        root = OutputLocations(runtime).resolve(token)
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('invalid source_id')
        if not (path / 'all/messages.jsonl').is_file():
            raise FileNotFoundError('archive missing')
        return path
    if source_id.startswith("export:"):
        name = source_id.split(":", 1)[1]
        if not name or "/" in name or name in {".", ".."}:
            raise ValueError("invalid source_id")
        path = (runtime.exports_root / name).resolve()
        if not path.is_relative_to(runtime.exports_root.resolve()):
            raise ValueError("invalid source_id")
        if not (path / "all" / "messages.jsonl").is_file():
            raise FileNotFoundError("archive missing")
        return path
    raise ValueError("unknown source_id")
