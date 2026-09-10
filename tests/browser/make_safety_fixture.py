"""Write explicit synthetic attack-shaped fixtures only into a new owned root."""
import argparse
import json
from collections import Counter
from pathlib import Path
from tests.test_export_service import _demo_tree
from wechat_export.archive_index import build_index
from wechat_export.export_service import QuerySpec, write_slice
from wechat_export.offline_html import write_offline_html


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True,type=Path);args=parser.parse_args()
    root=args.root.resolve();root.mkdir(exist_ok=True)
    archive=_demo_tree(root/'archive')
    path=archive/'all/messages.jsonl';rows=[json.loads(line) for line in path.read_text().splitlines()]
    base=rows[0]
    remote='https://must-not-fetch.invalid/resource'
    attack='globalThis.wlaAttack=1'
    cases=[
        ('voice','voice',f'<voicemsg voicelength="5200" aeskey="SYNTHETIC_CREDENTIAL" voiceurl="{remote}"/>'),
        ('video','video',f'<videomsg aeskey="SYNTHETIC_CREDENTIAL" cdnvideourl="{remote}"/>'),
        ('system','system','<sysmsg credential="SYNTHETIC_CREDENTIAL"/>'),
        ('unknown','unknown_999','<unknown_payload credential="SYNTHETIC_CREDENTIAL"/>'),
        ('empty-root','text','<msg/>'),
        ('image-title','image','<msg><img/><title><![CDATA[<payload aeskey="SYNTHETIC_CREDENTIAL"/>]]></title></msg>'),
        ('link','app',f'<msg><appmsg><title><![CDATA[Link <img src="{remote}" onerror="{attack}">]]></title><url>{remote}</url><aeskey>SYNTHETIC_CREDENTIAL</aeskey></appmsg></msg>'),
        ('literal','text',f'Literal markup: <img src="{remote}" onerror="{attack}"> remains literal.'),
        ('entity','app',f'<!DOCTYPE msg [<!ENTITY outside SYSTEM "{remote}">]><msg>&outside;</msg>'),
        ('quote','app','<msg><appmsg><title>Quote fixture</title><refermsg><displayname>Alice</displayname><content>Quoted safe content</content></refermsg></appmsg></msg>'),
        ('forward','app','<msg><appmsg><title>Forward fixture</title><recorditem><![CDATA[<recordinfo><dataitem><sourcename>Alice</sourcename><datadesc>Forwarded safe content</datadesc></dataitem></recordinfo>]]></recorditem></appmsg></msg>'),
        ('name-prefix','text','Alice:\nplease keep this line'),
        ('long','text','Long synthetic word: '+'x'*12000),
        ('search-private','text','needle-alpha-private'),
    ]
    for i,(uid,kind,text) in enumerate(cases):
        rows.append(dict(base,record_uid=uid,message_type_normalized=kind,text=text,
                         timestamp_utc=f'2026-01-03T00:{i:02}:00+00:00',sender_display_name=f'Alice <img src="{remote}" onerror="{attack}">'))
    rows.append(dict(rows[2],record_uid='search-room',text='needle-beta-room'))
    path.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows))
    counts=Counter(r['conversation_id'] for r in rows)
    convpath=archive/'all/conversations.jsonl'
    convs=[json.loads(line) for line in convpath.read_text().splitlines()]
    for c in convs:
        c['count']=counts[c['conversation_id']]
        times=[r['timestamp_utc'] for r in rows if r['conversation_id']==c['conversation_id']]
        c['first_timestamp_utc']=min(times);c['last_timestamp_utc']=max(times)
    convpath.write_text(''.join(json.dumps(c)+'\n' for c in convs))
    manifest=json.loads((archive/'manifest.json').read_text());manifest['record_count']=len(rows)
    (archive/'manifest.json').write_text(json.dumps(manifest));build_index(archive)
    outputs={}
    for fmt in ('jsonl','html'):
        result=write_slice(None,archive,QuerySpec.from_mapping({'scope':{'kind':'all'},'mode':'analysis','format':fmt}),
                           source='canonical',jobs_root=root/'outputs')
        outputs[fmt]=result['path']
    raw=write_slice(None,archive,QuerySpec.from_mapping({'scope':{'kind':'all'},'mode':'raw','format':'jsonl'}),
                    source='canonical',jobs_root=root/'outputs')
    assert 'SYNTHETIC_CREDENTIAL' in Path(raw['path']).read_text()
    assert 'SYNTHETIC_CREDENTIAL' not in Path(outputs['jsonl']).read_text()
    outputs['standalone']=str(write_offline_html(root/'standalone.html','Fixture <script>globalThis.wlaAttack=1</script>',iter(rows)))
    report={'synthetic':True,'archive':str(archive),'outputs':outputs,'record_count':len(rows)}
    (root/'fixture.json').write_text(json.dumps(report))
    print(json.dumps({'synthetic':True,'record_count':len(rows)}))


if __name__=='__main__':main()
