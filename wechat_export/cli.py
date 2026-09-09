from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from wechat_export import PARSER_VERSION
from wechat_export.config import DEFAULT_CONFIG_PATH, AppConfig, default_config_payload, load_config
from wechat_export.environment import collect_environment
from wechat_export.export_run import collect_records, export_records
from wechat_export.fsutil import ensure_dir, write_json
from wechat_export.inspect_livedb import inspect_livedb
from wechat_export.keys import key_access_status
from wechat_export.decrypt_livedb import decrypt_tree, load_raw_keys
from wechat_export.livedb_snapshot import snapshot_livedb_tree
from wechat_export.passphrase_probe import probe_account_passphrases
from wechat_export.protobuf_lite import walk as walk_protobuf
from wechat_export.rmfh import inspect_tree
from wechat_export.snapshot import make_snapshot_id, snapshot_backup_account


def _cfg(args: argparse.Namespace) -> AppConfig:
    return load_config(getattr(args, "config", None))


def cmd_init_config(args: argparse.Namespace) -> int:
    path = Path(args.config or DEFAULT_CONFIG_PATH)
    if path.exists() and not args.force:
        print(f"config exists: {path}", file=sys.stderr)
        return 1
    ensure_dir(path.parent)
    payload = default_config_payload()
    write_json(path, payload)
    os.chmod(path.parent, 0o700)
    print(f"wrote {path}")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    result = snapshot_backup_account(cfg, snapshot_id=args.snapshot_id)
    print(json.dumps({k: result[k] for k in ("snapshot_id", "status", "raw_preservation_complete", "dest_root")}, indent=2))
    return 0 if result["status"] == "ok" else 2


def cmd_inspect(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    out_dir = ensure_dir(cfg.reports_root / "inspect")
    if args.source == "backup2":
        root = Path(args.root) if args.root else cfg.source_backup2
        report = inspect_tree(root)
        write_json(out_dir / "backup2-probe.json", report)
        # metadata that is not RMFH
        meta = {}
        for name in ("detail.dat", "pkg_info.dat"):
            p = root / name
            if p.exists():
                data = p.read_bytes()
                meta[name] = {"size": len(data), "fields": walk_protobuf(data)}
        write_json(out_dir / "backup2-metadata-fields.json", meta)
        print(json.dumps({"source": "backup2", "root": str(root), "file_count": report["file_count"], "by_class": report["by_class"]}, indent=2))
        return 0
    if args.source == "live-db":
        root = Path(args.root) if args.root else cfg.live_db_root
        report = inspect_livedb(root)
        write_json(out_dir / "db-probe.json", report)
        print(json.dumps({"source": "live-db", "root": str(root), "db_count": report["db_count"], "sqlite_header_count": report["sqlite_header_count"]}, indent=2))
        return 0
    print("unknown source", file=sys.stderr)
    return 2


def cmd_probe(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    env = collect_environment()
    if args.source == "live-db":
        status = key_access_status(cfg, env.get("wechat_pids") or [])
        print(json.dumps({"source": "live-db", "result": status["result"], "keys_file_present": status["keys_file_present"], "attach_denied": status["attach_probe"].get("attach_denied")}, indent=2))
        return 0 if status["result"] != "key_access_blocked" else 3
    if args.source == "backup2":
        report = inspect_tree(cfg.source_backup2)
        write_json(cfg.reports_root / "inspect" / "backup2-probe.json", report)
        print(json.dumps({"source": "backup2", "text_decode_complete": False, "reason": "rmfh_key_unknown", "file_count": report["file_count"]}, indent=2))
        return 3
    return 2


def cmd_passphrase_probe(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    result = probe_account_passphrases(cfg)
    print(json.dumps({"status": result.get("status"), "any_hmac_ok": result.get("any_hmac_ok"), "candidate_count": result.get("candidate_count")}, indent=2))
    return 0 if result.get("any_hmac_ok") else 3


def cmd_ingest(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    if args.source == "backup2":
        print(json.dumps({"source": "backup2", "status": "blocked", "reason": "rmfh_key_unknown"}, indent=2))
        return 3
    if not args.decrypted_root:
        print(json.dumps({"source": "live-db", "status": "blocked", "reason": "key_access_blocked", "hint": "pass --decrypted-root after HMAC-verified decrypt"}, indent=2))
        return 3
    root = Path(args.decrypted_root)
    records, targets, meta = collect_records(root, cfg, "live-db", None)
    write_json(cfg.reports_root / "ingest-meta.json", {"targets_counts": {k: len(v) for k, v in targets.items()}, **meta, "record_count": len(records)})
    print(json.dumps({"source": "live-db", "record_count": len(records), "target_candidate_counts": {k: len(v) for k, v in targets.items()}}, indent=2))
    return 0


def cmd_conversations(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    if not args.decrypted_root:
        print(json.dumps({"status": "blocked", "reason": "key_access_blocked"}, indent=2))
        return 3
    _records, targets, _meta = collect_records(Path(args.decrypted_root), cfg, "live-db", None)
    print(json.dumps({"target_candidate_counts": {k: len(v) for k, v in targets.items()}, "ambiguous": {k: len(v) != 1 for k, v in targets.items()}}, indent=2))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    if not args.decrypted_root:
        print(json.dumps({"status": "blocked", "reason": "key_access_blocked", "source_kind": None}, indent=2))
        return 3
    run_id = args.run_id or make_snapshot_id(tz_name=cfg.display_timezone)
    records, targets, meta = collect_records(Path(args.decrypted_root), cfg, "live-db", None)
    if args.conversation_id:
        records = [r for r in records if r.conversation_id == args.conversation_id]
    out = export_records(
        records,
        targets,
        cfg,
        run_id,
        source_kind="live-db",
        backup2_coverage="unverified",
        extra_notes=["export from decrypted live-db copies", f"meta={meta}"],
    )
    print(json.dumps({"status": "ok", "run_id": run_id, "out": str(out), "record_count": len(records), "source_kind": "live-db", "backup2_coverage": "unverified"}, indent=2))
    return 0


def cmd_snapshot_livedb(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    dest = Path(args.dst) if args.dst else cfg.work_root / make_snapshot_id(tz_name=cfg.display_timezone) / "live-db"
    summary = snapshot_livedb_tree(cfg.live_db_root, dest)
    print(
        json.dumps(
            {
                "dst_root": summary["dst_root"],
                "db_count": summary["db_count"],
                "hot_copies": summary["hot_copies"],
                "idle_copies": summary["idle_copies"],
                "wechat_pids": summary["wechat_pids"],
                "consistency_note": "hot_copy_unverified means WeChat had the files open; WAL was copied but not merged",
            },
            indent=2,
        )
    )
    return 0


def cmd_decrypt(args: argparse.Namespace) -> int:
    keys = load_raw_keys(Path(args.keys_file))
    src = Path(args.src)
    dst = Path(args.dst)
    if dst.exists() and any(dst.iterdir()):
        print("error: dest must be empty", file=sys.stderr)
        return 1
    summary = decrypt_tree(src, dst, keys)
    print(
        json.dumps(
            {
                "ok": summary["ok"],
                "failed": summary["failed"],
                "wal_merged": summary["wal_merged"],
                "wal_unmerged": summary["wal_unmerged"],
                "dst": summary["dst"],
                "params_provenance": summary["params_provenance"],
                "wechat_live_db_params_verified": summary["wechat_live_db_params_verified"],
            },
            indent=2,
        )
    )
    return 0 if summary["failed"] == 0 else 2


def cmd_index(args: argparse.Namespace) -> int:
    from wechat_export.archive_index import build_index

    summary = build_index(Path(args.export_dir), Path(args.index) if args.index else None)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from wechat_export.archive_server import serve

    serve(Path(args.export_dir), host=args.host, port=args.port)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    snap = sorted((cfg.raw_root).glob("*"))
    latest = snap[-1] if snap else None
    ver = None
    if latest and (latest / "copy-verification.json").exists():
        ver = json.loads((latest / "copy-verification.json").read_text(encoding="utf-8"))
    print(json.dumps({
        "parser_version": PARSER_VERSION,
        "raw_preservation_complete": bool(ver and ver.get("raw_preservation_complete")),
        "latest_snapshot": str(latest) if latest else None,
        "backup2_coverage": "unverified",
        "live_db_text_decode_complete": False,
    }, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wechat-export")
    p.add_argument("--config", default=None, help="path to private config.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init-config")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init_config)

    s = sub.add_parser("snapshot")
    s.add_argument("--snapshot-id", default=None)
    s.set_defaults(func=cmd_snapshot)

    s = sub.add_parser("snapshot-livedb")
    s.add_argument("--dst", default=None)
    s.set_defaults(func=cmd_snapshot_livedb)

    s = sub.add_parser("inspect")
    s.add_argument("--source", required=True, choices=["backup2", "live-db"])
    s.add_argument("--root", default=None)
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("probe")
    s.add_argument("--source", required=True, choices=["backup2", "live-db"])
    s.set_defaults(func=cmd_probe)

    s = sub.add_parser("passphrase-probe")
    s.set_defaults(func=cmd_passphrase_probe)

    s = sub.add_parser("ingest")
    s.add_argument("--source", required=True, choices=["backup2", "live-db"])
    s.add_argument("--resume", action="store_true")
    s.add_argument("--decrypted-root", default=None)
    s.set_defaults(func=cmd_ingest)

    s = sub.add_parser("conversations")
    s.add_argument("--decrypted-root", default=None)
    s.set_defaults(func=cmd_conversations)

    s = sub.add_parser("export")
    s.add_argument("--scope", choices=["all"])
    s.add_argument("--conversation-id")
    s.add_argument("--decrypted-root", default=None)
    s.add_argument("--run-id", default=None)
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("decrypt")
    s.add_argument("--keys-file", required=True)
    s.add_argument("--src", required=True)
    s.add_argument("--dst", required=True)
    s.set_defaults(func=cmd_decrypt)

    s = sub.add_parser("verify")
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("index")
    s.add_argument("--export-dir", required=True)
    s.add_argument("--index", default=None)
    s.set_defaults(func=cmd_index)

    s = sub.add_parser("serve")
    s.add_argument("--export-dir", required=True)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
