import argparse,contextlib,io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from tests.test_export_pipeline import _build_dbs,_cfg
from wechat_export.cli import cmd_export

class StreamingCliTests(unittest.TestCase):
    def test_disk_cli_matches_expected_count_and_publication(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cfg=_cfg(root);dec=_build_dbs(root)
            args=argparse.Namespace(decrypted_root=str(dec),run_id='cli-run',snapshot_id='fixture',conversation_id=None)
            out=io.StringIO()
            with patch('wechat_export.cli._cfg',return_value=cfg),contextlib.redirect_stdout(out):
                self.assertEqual(cmd_export(args),0)
            result=json.loads(out.getvalue())
            self.assertEqual(result['record_count'],5)
            self.assertTrue((cfg.exports_root/'cli-run/all/messages.jsonl').is_file())
            self.assertEqual(list(cfg.work_root.glob('normalize-*')),[])
            from wechat_export.scratch import NAMESPACE
            self.assertEqual(list((cfg.work_root/NAMESPACE).glob('scratch-*')),[])
            with patch('wechat_export.cli._cfg',return_value=cfg):
                with self.assertRaises(FileExistsError):cmd_export(args)

    def test_cli_failure_does_not_publish_partial_archive(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cfg=_cfg(root);dec=_build_dbs(root)
            args=argparse.Namespace(decrypted_root=str(dec),run_id='cli-fail',snapshot_id='fixture',conversation_id=None)
            def fail(records,targets,stage_cfg,run_id,**kwargs):
                partial=stage_cfg.exports_root/run_id/'all'
                partial.mkdir(parents=True)
                (partial/'messages.jsonl').write_text('partial')
                raise RuntimeError('synthetic failure')
            with patch('wechat_export.cli._cfg',return_value=cfg),patch('wechat_export.cli.export_records',side_effect=fail):
                with self.assertRaises(RuntimeError):cmd_export(args)
            self.assertFalse((cfg.exports_root/'cli-fail').exists())
            self.assertEqual(list(cfg.work_root.glob('normalize-*')),[])
            from wechat_export.scratch import NAMESPACE
            self.assertEqual(list((cfg.work_root/NAMESPACE).glob('scratch-*')),[])
