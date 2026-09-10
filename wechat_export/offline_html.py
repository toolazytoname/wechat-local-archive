"""Streaming offline HTML: escaped text, no external dependencies, shared CSP."""
from __future__ import annotations
import html
import os
from pathlib import Path
from typing import Iterable
from wechat_export.preview import analysis_record
from wechat_export.message_cards import card_text
from wechat_export.link_urls import safe_web_url

CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
CSS = """
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;background:#f5f5f5;margin:0;color:#111;overflow-wrap:anywhere}
main{max-width:720px;width:100%;margin:0 auto;padding:24px}
h1{font-size:18px}
.meta{color:#656565;font-size:12px}
.day{text-align:center;color:#656565;font-size:12px;margin:16px 0}
.row{margin:12px 0}
.row.self{text-align:right}
.bubble,.card{display:inline-block;max-width:86%;text-align:left;background:#fff;padding:8px 10px;border-radius:4px;white-space:pre-wrap;overflow-wrap:anywhere}
.self .bubble{background:#95ec69}
.card{border:1px solid #e5e5e5;font-size:13px}
@media(max-width:480px){main{padding:16px 10px}.bubble,.card{max-width:94%}}
"""
FOOTER = '</main></body></html>'


def document_header(title: str, extra_note: str = '') -> str:
    return ('<!doctype html><html lang="zh-Hans"><head><meta charset="utf-8">'
            '<meta name="referrer" content="no-referrer">'
            '<meta http-equiv="Content-Security-Policy" content="' + html.escape(CSP, quote=True) + '">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + html.escape(title) + '</title><style>' + CSS + '</style></head><body><main>'
            '<h1>' + html.escape(title) + '</h1><p class="meta">'
            '离线消息快照 · source_kind=live-db · backup2_coverage=unverified。'
            '不包含附件二进制；请同时保留 manifest、coverage 和附件清单。</p>'
            + ('<p class="meta">' + html.escape(extra_note) + '</p>' if extra_note else ''))


def document_row(timestamp: str, who: str, text: str, is_self: bool, *, card=False, link_url=None) -> str:
    row_class = 'row self' if is_self else 'row'
    content_class = 'card' if card else 'bubble'
    destination = safe_web_url(link_url)
    link = ('<p><a href="' + html.escape(destination, quote=True)
            + '" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">'
              '打开原链接（访问外网）</a></p>') if destination else ''
    return ('<div class="' + row_class + '"><p class="meta">' + html.escape(timestamp + ' ' + who)
            + '</p><div class="' + content_class + '">' + html.escape(text) + link + '</div></div>')


def write_offline_html(path: Path, title: str, records: Iterable[dict], *, extra_note: str = '') -> Path:
    """Legacy direct writer: private streaming stage, exclusive publication.

    A caller-supplied existing output is never overwritten. Canonical records
    are projected; legacy preformatted records without a type remain escaped.
    """
    from wechat_export.scratch import ScratchSpace
    from wechat_export.attachment_accounting import attachment_note
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with ScratchSpace(path.parent, 'offline-html') as temporary:
        stage = temporary.payload/'document.html'
        with stage.open('w',encoding='utf-8') as stream:
            stream.write(document_header(title, extra_note))
            last_day = ''
            for rec in records:
                if rec.get('export_mode') != 'analysis' and any(key in rec for key in ('message_type_normalized','message_type','payload_kind')):
                    rec = analysis_record(rec)
                ts = str(rec.get('timestamp_utc') or '')
                day = ts[:10]
                if day and day != last_day:
                    stream.write('<div class="day">' + html.escape(day) + '</div>')
                    last_day = day
                text = str(rec.get('text') or rec.get('preview') or '[未知消息]')
                details = card_text(rec.get('card'))
                note = attachment_note(rec.get('attachment_summary'))
                if details: text += '\n' + details
                if note: text += '\n' + note
                stream.write(document_row(ts,str(rec.get('sender_display_name') or ''),text,
                                          bool(rec.get('is_self')),card=rec.get('readable') is False,
                                          link_url=(rec.get('card') or {}).get('url')))
            stream.write(FOOTER)
            stream.flush();os.fsync(stream.fileno())
        stage.chmod(0o600)
        # Same-parent filesystem; hard-link creation is exclusive, even under a
        # concurrent writer. Receipt cleanup removes the private staging link.
        os.link(stage,path)
    return path
