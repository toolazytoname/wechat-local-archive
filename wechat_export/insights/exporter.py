"""Portable, local-only profile reports with evidence and source provenance."""
from __future__ import annotations
import hashlib
import html
import json
import os
import shutil
import uuid
from pathlib import Path
from .profile_pipeline import get_run
from .store import InsightStore, InsightsError


def export_profile(store: InsightStore, run_id: str, dest: Path) -> dict:
    run = get_run(store, run_id)
    if dest.exists():
        raise InsightsError('Output already exists', 'output_exists')
    staging = dest.with_name('.' + dest.name + '-' + uuid.uuid4().hex)
    staging.mkdir(parents=True, mode=0o700)
    try:
        title = '我的记录画像' if run['kind'] == 'self' else '好友记录画像'
        notice = '这份报告基于聊天记录中的原话，不能替代对人的完整了解。收藏、引用和假设不代表本人观点。'
        if run.get('is_stale'):
            notice += ' 此报告基于旧资料或旧身份设置，仅供历史回顾。'
        observations = [o for o in run['observations'] if o['review_state'] != 'excluded']
        payload = {**run, 'observations': observations,
                   'source_kind': 'live-db', 'backup2_coverage': 'unverified'}
        (staging/'report.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        md = [f'# {title}', '', notice, '', f"生成时间：{run['created_at']}",
              f"资料修订：{run['source_revision']}", '', '## 范围与覆盖', '',
              '```json', json.dumps(run['coverage'], ensure_ascii=False, indent=2), '```', '']
        blocks = []
        for obs in observations:
            md.extend(['## ' + obs['statement'], '', *obs['caveats'], ''])
            quotes = []
            for ev in obs['evidence']:
                quote = str(ev.get('quote') or '')
                md.extend(['> ' + quote.replace('\n','\n> '), '', f"来源记录：`{ev['record_uid']}`", ''])
                quotes.append('<blockquote>' + html.escape(quote) + '</blockquote><small>记录：' + html.escape(ev['record_uid']) + '</small>')
            blocks.append('<section><h2>' + html.escape(obs['statement']) + '</h2>' + ''.join(quotes) +
                          '<p>' + html.escape(' '.join(obs['caveats'])) + '</p></section>')
        (staging/'报告.md').write_text('\n'.join(md), encoding='utf-8')
        (staging/'开始阅读.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'"><title>'+html.escape(title)+'</title><style>body{max-width:780px;margin:40px auto;padding:0 24px;font:16px/1.8 system-ui;color:#243a30;background:#f5f7f3}section{background:white;padding:24px;margin:20px 0;border-radius:14px}blockquote{white-space:pre-wrap;border-left:3px solid #759986;padding-left:18px}h2{font-size:20px}small{overflow-wrap:anywhere}</style><h1>'+html.escape(title)+'</h1><p>'+html.escape(notice)+'</p><p>观察 '+str(len(observations))+' 条</p>'+''.join(blocks)+'</html>', encoding='utf-8')
        manifest = {'kind':'profile_report','run_id':run_id,'observation_count':len(observations),
                    'source_kind':'live-db','backup2_coverage':'unverified','source_revision':run['source_revision'],
                    'is_stale':run.get('is_stale',False)}
        (staging/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        sums=[]
        for path in sorted(staging.iterdir()):
            path.chmod(0o600)
            sums.append(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name)
        (staging/'SHA256SUMS').write_text('\n'.join(sums)+'\n');(staging/'SHA256SUMS').chmod(0o600)
        os.rename(staging,dest)
        return {'path':str(dest),'run_id':run_id,'observation_count':len(observations)}
    finally:
        if staging.exists():shutil.rmtree(staging)
