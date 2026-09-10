import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from wechat_export.desktop import launch,health

class DesktopTests(unittest.TestCase):
    def test_invalid_health_does_not_accept_other_listener(self):
        self.assertFalse(health({'port':0,'token':'synthetic','pid':1}))
        self.assertFalse(health({}))
    def test_live_receipt_reuses_process_and_opens_local_url(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve();runtime=root/'runtime';state=runtime/'desktop';state.mkdir(parents=True,mode=0o700)
            config=root/'config.json';config.write_text(json.dumps({'runtime':str(runtime)}))
            receipt={'port':9999,'token':'synthetic','pid':123,'config':str(config),'archive':'None','process_identity':'owned'}
            (state/'service.json').write_text(json.dumps(receipt))
            with patch('wechat_export.desktop.health',return_value=True),patch('wechat_export.desktop.subprocess.run') as run,patch('wechat_export.desktop.subprocess.Popen') as start:
                result=launch(config)
                self.assertEqual(result['url'],'http://127.0.0.1:9999/?home=1')
                start.assert_not_called();self.assertEqual(run.call_args.args[0],['open',result['url']])
    def test_unresponsive_but_live_is_not_restarted(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve();runtime=root/'runtime';state=runtime/'desktop';state.mkdir(parents=True,mode=0o700)
            config=root/'config.json';config.write_text(json.dumps({'runtime':str(runtime)}))
            (state/'service.json').write_text(json.dumps({'port':9999,'token':'synthetic','pid':123,'config':str(config),'archive':'None','process_identity':'owned'}))
            with patch('wechat_export.desktop.health',return_value=False),patch('wechat_export.desktop._identity',return_value='owned'),patch('wechat_export.desktop.subprocess.Popen') as start:
                with self.assertRaisesRegex(RuntimeError,"服务仍在运行"):launch(config)
                start.assert_not_called()

    def test_framework_exec_does_not_change_worker_identity(self):
        from wechat_export.desktop import _identity
        import subprocess
        prefix='Wed Sep  9 18:00:00 2026'
        args=' -I -m wechat_export.desktop --worker --config /synthetic/config.json --port 8888'
        def response(command,**kwargs):
            return subprocess.CompletedProcess(command,0,prefix if command[-1]=='lstart=' else prefix+' /synthetic/venv/python'+args,'')
        def framework(command,**kwargs):
            return subprocess.CompletedProcess(command,0,prefix if command[-1]=='lstart=' else prefix+' /synthetic/framework/Python'+args,'')
        with patch('wechat_export.desktop.subprocess.run',side_effect=response):first=_identity(1)
        with patch('wechat_export.desktop.subprocess.run',side_effect=framework):second=_identity(1)
        self.assertEqual(first,second)
