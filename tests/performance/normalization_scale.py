"""Opt-in synthetic normalization -> all outputs -> index stress, no WeChat access.
Run: .venv/bin/python -m tests.performance.normalization_scale --extra-records 500000
"""
import argparse,json,resource,sqlite3,tempfile,time
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tests.test_export_pipeline import _build_dbs,_cfg
from wechat_export.export_run import collect_records,export_records
from wechat_export.archive_index import build_index
from wechat_export.record_store import RecordStore
from wechat_export.schema import msg_table_name


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--extra-records',type=int,default=500000)
    n=parser.parse_args().extra_records
    if not 0<n<=2000000:raise ValueError('invalid synthetic row count')
    start=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='wla-normalization-scale-') as td:
        root=Path(td);dec=_build_dbs(root);cfg=replace(_cfg(root),target_names=())
        table=msg_table_name('wxid_alice')
        with closing(sqlite3.connect(dec/'message/message_0.db')) as conn:
            conn.execute(f'''WITH RECURSIVE seq(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM seq WHERE i<?)
            INSERT INTO "{table}"(server_id,local_type,sort_seq,real_sender_id,create_time,status,message_content)
            SELECT i+100,1,i+100,2,1700000300+i,2,'Synthetic scale record '||i FROM seq''',(n,))
            conn.commit()
        baseline=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        store=RecordStore(root/'normalized.sqlite')
        try:
            records,targets,_=collect_records(dec,cfg,'live-db','synthetic-scale',record_store=store)
            assert records is store and len(records)==n+5
            out=export_records(records,targets,cfg,'scale',source_kind='live-db',backup2_coverage='unverified',extra_notes=['synthetic performance fixture'])
        finally:store.close()
        summary=build_index(out)
        assert summary['message_count']==n+5
        with (out/'all/messages.jsonl').open() as fh:assert sum(1 for _ in fh)==n+5
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS reports bytes. Linux reports KiB.
        import sys
        factor=1 if sys.platform=='darwin' else 1024
        result={'records':n+5,'normalization_storage':'disk_sqlite','baseline_rss_mb':round(baseline*factor/1048576,2),
                'peak_rss_mb':round(peak*factor/1048576,2),'elapsed_seconds':round(time.monotonic()-start,2),
                'all_formats_and_index':True,'source':'synthetic_decrypted_databases'}
        print(json.dumps(result))
        if peak*factor>256*1048576:raise AssertionError('synthetic normalization exceeded 256 MiB RSS budget')
if __name__=='__main__':main()
