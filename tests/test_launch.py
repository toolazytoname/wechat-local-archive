from __future__ import annotations

import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from wechat_export.cli import build_parser, cmd_launch
from wechat_export.runtime import pick_loopback_port, resolve_runtime, resolve_source_id


class LaunchTests(unittest.TestCase):
    def test_parser_does_not_require_export_dir(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["launch", "--port", "8767"])
        self.assertEqual(args.cmd, "launch")
        self.assertIsNone(args.export_dir)
        self.assertTrue(hasattr(args, "demo"))

    def test_serve_still_requires_export_dir(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["serve"])

    def test_pick_port_skips_busy(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        busy = sock.getsockname()[1]
        try:
            chosen = pick_loopback_port(busy, span=5)
            self.assertNotEqual(chosen, busy)
        finally:
            sock.close()

    def test_runtime_uses_explicit_data_root_without_deleting(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "keep-me"
            marker.write_text("ok", encoding="utf-8")
            runtime = resolve_runtime(td)
            self.assertEqual(runtime.kind, "env")
            self.assertTrue(marker.exists())
            self.assertTrue(runtime.private_root.is_dir())

    def test_unknown_source_id_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = resolve_runtime(td)
            with self.assertRaises(ValueError):
                resolve_source_id("/etc/passwd", runtime)
            with self.assertRaises(ValueError):
                resolve_source_id("export:../x", runtime)

    def test_launch_starts_without_archive(self) -> None:
        started = threading.Event()

        def fake_serve(export_dir, host="127.0.0.1", port=8765, runtime=None):
            self.assertIsNone(export_dir)
            self.assertEqual(host, "127.0.0.1")
            started.set()

        parser = build_parser()
        args = parser.parse_args(["launch", "--port", "8799"])
        with patch("wechat_export.runtime.pick_loopback_port", return_value=8799), patch(
            "wechat_export.archive_server.serve", side_effect=fake_serve
        ):
            rc = cmd_launch(args)
        self.assertEqual(rc, 0)
        self.assertTrue(started.is_set())
