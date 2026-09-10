"""Full canonical outputs without retaining messages or monthly groups in memory."""
from __future__ import annotations
import csv
import hashlib
import json
from collections import Counter, OrderedDict
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from wechat_export.fsutil import ensure_dir, write_json, sha256_file
from wechat_export.export_service import csv_safe_cell
from wechat_export.preview import analysis_record
from wechat_export.message_cards import card_text
from wechat_export.source_ledger import SCHEMA_VERSION
from wechat_export.writers import safe_label
from wechat_export.attachment_accounting import AttachmentAccounting, attachment_note

FIELDS=('record_uid','conversation_id','conversation_display_name','sender_id','sender_display_name',
        'is_self','timestamp_utc','message_type_normalized','text','server_message_id','source_kind',
        'source_snapshot_id','source_relative_path','parse_status','schema_version','attachment_summary')

def request_digest(targets, cfg, source_kind, backup2_coverage, extra_notes):
    request = {'targets': targets, 'timezone': cfg.display_timezone, 'source_kind': source_kind,
               'backup2_coverage': backup2_coverage, 'notes': extra_notes}
    return hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class MonthlyFiles:
    def __init__(self, limit=24):
        self.limit=limit;self.opened=OrderedDict();self.created=set()
    def write(self,path,title,line):
        fh=self.opened.pop(path,None)
        if fh is None:
            ensure_dir(path.parent)
            fh=path.open('a',encoding='utf-8')
            path.chmod(0o600)
            if path not in self.created:
                fh.write('# '+title+'\n\n');self.created.add(path)
        self.opened[path]=fh
        fh.write(line)
        if len(self.opened)>self.limit:self.opened.popitem(last=False)[1].close()
    def close(self):
        for fh in self.opened.values():fh.close()
        self.opened.clear()


def export_stream(records,targets,cfg,run_id,*,source_kind,backup2_coverage,extra_notes):
    out=cfg.exports_root/run_id
    # Unique staging is required for streaming append handles; never append old output.
    ensure_dir(out.parent)
    out.mkdir(mode=0o700,exist_ok=False)
    tz=ZoneInfo(cfg.display_timezone)
    convos={};counts=Counter();types=Counter();statuses=Counter()
    target_info={};target_by_cid={}
    label_counts=Counter(safe_label(name,'target') for name in targets)
    target_labels={name: safe_label(name,'target') + ('__'+hashlib.sha256(name.encode()).hexdigest()[:12] if label_counts[safe_label(name,'target')]>1 else '') for name in targets}
    for name,matches in targets.items():
        cid=matches[0]['username'] if len(matches)==1 else None
        target_info[name]={'match_count':len(matches),'ambiguous':len(matches)!=1,
                           'chosen_conversation_id':cid,'exported_records':0}
        if cid:target_by_cid.setdefault(cid,[]).append(name)
    monthly=MonthlyFiles()
    snapshot=None;parser=None;n=0;first=last=None
    try:
        with ExitStack() as stack:
            def output(rel):
                path=out/rel;ensure_dir(path.parent)
                fh=stack.enter_context(path.open('w',encoding='utf-8',newline=''))
                path.chmod(0o600)
                return fh
            canonical=output('all/messages.jsonl')
            attachments=AttachmentAccounting(output('attachment-ledger.jsonl'))
            csv_out=csv.DictWriter(output('all/messages.csv'),fieldnames=FIELDS,extrasaction='ignore');csv_out.writeheader()
            target_outputs={}
            for name in targets:
                label=target_labels[name]
                jf=output(f'targets/{label}/messages.jsonl')
                cf=csv.DictWriter(output(f'targets/{label}/messages.csv'),fieldnames=FIELDS,extrasaction='ignore');cf.writeheader()
                target_outputs[name]=(jf,cf,out/'targets'/label,AttachmentAccounting(output(f'targets/{label}/attachment-ledger.jsonl')))
            for rec in records:
                raw=rec.to_dict();line=json.dumps(raw,ensure_ascii=False,sort_keys=True)+'\n'
                canonical.write(line)
                attachments.observe(raw)
                # CSV/Markdown are readable analysis views; canonical JSONL is raw.
                analysis=analysis_record(raw)
                csvrow={k:csv_safe_cell(json.dumps(analysis.get(k),ensure_ascii=False,sort_keys=True) if k=='attachment_summary' else analysis.get(k)) for k in FIELDS}
                csv_out.writerow(csvrow)
                n+=1;types[rec.message_type_normalized]+=1;statuses[rec.parse_status]+=1
                counts['unknown_senders']+=not bool(rec.sender_id)
                parser=parser or rec.parser_version;snapshot=snapshot or rec.source_snapshot_id
                ts=rec.timestamp_utc
                if ts:
                    first=min(first,ts) if first else ts;last=max(last,ts) if last else ts
                conv=convos.setdefault(rec.conversation_id,{'conversation_id':rec.conversation_id,
                    'conversation_type':rec.conversation_type,'conversation_display_name':rec.conversation_display_name,
                    'count':0,'first_timestamp_utc':ts,'last_timestamp_utc':ts})
                conv['count']+=1
                if ts:
                    conv['first_timestamp_utc']=min(conv['first_timestamp_utc'] or ts,ts)
                    conv['last_timestamp_utc']=max(conv['last_timestamp_utc'] or ts,ts)
                month=datetime.fromisoformat(ts).astimezone(tz).strftime('%Y-%m') if ts else 'unknown-month'
                name=rec.conversation_display_name or rec.conversation_id
                body=analysis['text'] if analysis['readable'] else analysis['preview']
                details=card_text(analysis.get('card'))
                if details:body += '\n'+details
                note=attachment_note(analysis.get('attachment_summary'))
                if note:body += '\n'+note
                md=f'- {ts or "unknown-time"} {rec.sender_display_name or rec.sender_id or "unknown"}: {body}\n'
                unique=hashlib.sha256(rec.conversation_id.encode()).hexdigest()[:16]
                directory=safe_label(name,'conversation')+'__'+unique
                monthly.write(out/'conversations'/directory/(month+'.md'),name+' '+month,md)
                for target in target_by_cid.get(rec.conversation_id,[]):
                    jf,cf,folder,counter=target_outputs[target];jf.write(line);cf.writerow(csvrow)
                    counter.observe(raw)
                    target_info[target]['exported_records']+=1
                    monthly.write(folder/(month+'.md'),name+' '+month,md)
            convfile=output('all/conversations.jsonl')
            for cid in sorted(convos):convfile.write(json.dumps(convos[cid],ensure_ascii=False,sort_keys=True)+'\n')
    finally:
        monthly.close()
    partial=n-statuses['ok']
    state='selected_source_exported' if n else 'blocked'
    if n and (partial or any(x['ambiguous'] for x in target_info.values())):state='partial'
    from wechat_export.export_service import selection_accounting
    write_json(out/'manifest.json',{'selection_accounting':selection_accounting(n,n,False),'schema_version':SCHEMA_VERSION,'run_id':run_id,'source_kind':source_kind,
        'source_snapshot_id':snapshot,'backup2_coverage':backup2_coverage,'record_count':n,
        'partial_parse_records':partial,'targets':target_info,'export_status':state,
        'export_status_meaning':'selected_source_exported','attachment_extraction_complete':False,
        'parser_version':parser,'display_timezone':cfg.display_timezone,'csv_markdown_mode':'analysis',
        'normalization_storage':'disk_sqlite' if records.__class__.__name__=='RecordStore' else 'provided_iterable',
        'max_open_monthly_files':monthly.limit,'attachment_accounting':attachments.summary(),
        'attachment_ledger':'attachment-ledger.jsonl'})
    for name, (_,_,folder,counter) in target_outputs.items():
        write_json(folder/'manifest.json',{'source_kind':source_kind,'backup2_coverage':backup2_coverage,
                   'selection_accounting':selection_accounting(target_info[name]['exported_records'],target_info[name]['exported_records'],False),
                   'scope':'configured_target','target':target_info[name], 'record_count':target_info[name]['exported_records'],
                   'attachment_accounting':counter.summary(),'attachment_ledger':'attachment-ledger.jsonl',
                   'parent_manifest':'../../manifest.json'})
    write_json(out/'target-conversations.json',targets)
    (out/'quality-report.md').write_text('\n'.join(['# quality-report','',f'- source_kind: `{source_kind}`',
        f'- backup2_coverage: `{backup2_coverage}`',f'- record_count: {n}',f'- conversation_count: {len(convos)}',
        f'- parse_status: {dict(statuses)}',f'- type_distribution: {dict(types)}',
        f'- unknown_sender_count: {counts["unknown_senders"]}',f'- first_timestamp_utc: {first}',
        f'- last_timestamp_utc: {last}','',*['- '+note for note in extra_notes]])+'\n',encoding='utf-8')
    from wechat_export.coverage_report import refresh_full_coverage
    manifest=json.loads((out/'manifest.json').read_text())
    manifest['export_request_sha256']=request_digest(targets,cfg,source_kind,backup2_coverage,extra_notes)
    refresh_full_coverage(out,manifest)
    manifest['generated_files']={p.relative_to(out).as_posix():sha256_file(p) for p in out.rglob('*') if p.is_file() and p!=out/'manifest.json'}
    write_json(out/'manifest.json',manifest)
    return out
