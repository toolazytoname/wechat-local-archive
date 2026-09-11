"""Synthetic-only browser fixture. Never opens the operator's data or providers."""
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from tests.test_insights_identity import _msg, _tree
from wechat_export.archive_server import serve
from wechat_export.runtime import resolve_runtime
from wechat_export.insights.store import InsightsError


class BrowserProvider:
    kind = 'remote'
    engine_id = 'synthetic-ui'
    base_url = 'https://example.invalid'
    model = 'synthetic'

    def analyze(self, payload):
        time.sleep(.15)
        return {'observations':[{'statement':r['text'], 'dimension':'stated_plans',
                                 'evidence_ids':[r['record_uid']]} for r in payload['records'][:3]]}


class FailedBrowserProvider(BrowserProvider):
    engine_id="synthetic-failed"
    def analyze(self,payload):
        raise InsightsError("private provider output must not escape", "remote_auth")

def main():
    port=int(sys.argv[1])
    with tempfile.TemporaryDirectory(prefix='wla-ux-') as td:
        root=Path(td)
        records=[_msg(record_uid=f'me{i}',text=f'我计划每周花{i+1}小时读书。') for i in range(4)]
        records += [_msg(record_uid=f'alice{i}',sender_id='wxid_alice',is_self=False,text=f'我计划阅读第{i+1}本书。') for i in range(3)]
        records += [_msg(record_uid='bob1',conversation_id='wxid_bob',conversation_display_name='Bob',sender_id='wxid_bob',is_self=False,text='我喜欢户外活动。')]
        records += [_msg(record_uid='link1',conversation_id='room@chatroom',conversation_display_name='学习收藏（示例）',conversation_type='room',text='<msg><appmsg><title>学习方法示例</title><type>5</type><url>https://example.invalid/reading</url></appmsg></msg>',message_type_normalized='app')]
        archive=_tree(root/'archive',records)
        runtime=resolve_runtime(root/'runtime')
        engines={'default':'byok','engines':[{'id':'byok','available':True,'host':'example.invalid','model':'synthetic','needs_consent':True}, {'id':'grok_cli','available':True,'host':'example.invalid','model':'synthetic','needs_consent':True}]}
        import wechat_export.insights_routes as routes
        with patch.object(routes,'resolve_provider',side_effect=lambda *a,**k: FailedBrowserProvider() if k.get('engine')=='grok_cli' else BrowserProvider()),patch.object(routes,'public_engine_list',return_value=engines),patch.object(routes,'public_provider_view',return_value={'available':True,'remote':True,'host':'example.invalid','model':'synthetic'}):
            serve(archive,port=port,runtime=runtime)


if __name__=='__main__':main()
