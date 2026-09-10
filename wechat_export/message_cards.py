"""Bounded allowlisted card metadata; no remote loads, entity expansion or raw XML."""
from __future__ import annotations
import re
import xml.etree.ElementTree as ET
from wechat_export.link_urls import safe_web_url

MAX_XML_CHARS = 256_000
MAX_CARD_ITEMS = 8


def parse_xml(body):
    if len(body)>MAX_XML_CHARS or re.search(r'<!\s*(DOCTYPE|ENTITY)',body,re.I):
        return None
    try:
        root=ET.fromstring(body)
    except (ET.ParseError,ValueError):
        return None
    for i,node in enumerate(root.iter()):
        if i>5000:return None
    return root


def field(node,path,limit=500):
    child=node.find(path)
    if child is None or len(child):return None
    value=''.join(child.itertext()).strip()
    # Structured nested content is not human text. Never pass it through a card.
    if value.lstrip().startswith('<'):return None
    return value[:limit] or None


def card_details(body):
    root=parse_xml(body)
    if root is None:return {'kind':'unknown','parse_status':'unparsed','items':[]}
    app=root if root.tag=='appmsg' else root.find('.//appmsg')
    if app is None:return None
    title=field(app,'title')
    ref=app.find('refermsg')
    if ref is not None:
        return {'kind':'quote','parse_status':'parsed','title':title,
                'author':field(ref,'displayname',120),
                'quoted_text':field(ref,'content',1200) or '[引用内容未解析]',
                'items':[]}
    record=app.find('recorditem')
    if record is not None:
        raw=record.text or ''
        nested=parse_xml(raw) if raw.strip() else None
        if nested is None and len(record):nested=record
        items=[];total=0
        if nested is not None:
            for item in nested.iter('dataitem'):
                total+=1
                if len(items)<MAX_CARD_ITEMS:
                    items.append({'author':field(item,'sourcename',120),
                                  'text':field(item,'datadesc',800) or field(item,'datatitle',500) or '[非文字消息]'})
        return {'kind':'forwarded','parse_status':'parsed' if nested is not None else 'partial',
                'title':title,'items':items,'item_count':total if nested is not None else None,
                'truncated':total>MAX_CARD_ITEMS}
    attach=app.find('appattach')
    if attach is not None and (attach.find('fileext') is not None or attach.find('totallen') is not None):
        ext=field(attach,'fileext',16)
        if ext and not re.fullmatch(r'[A-Za-z0-9]{1,16}',ext):ext=None
        length=field(attach,'totallen',24)
        size=int(length) if length and length.isascii() and length.isdigit() and len(length)<20 else None
        return {'kind':'file','parse_status':'parsed','title':title,'extension':ext,'size_bytes':size,
                'availability':'not_verified','items':[]}
    # Read only the app-message webpage field, never CDN/attachment URL fields.
    destination = app.find('url')
    url = safe_web_url(destination.text) if destination is not None and not len(destination) else None
    return {'kind':'link','parse_status':'parsed','title':title,'description':field(app,'des',800),
            'url':url,'remote_open_disabled':False if url else True,
            'automatic_remote_load':False,'items':[]}


def card_text(card):
    """Text-only counterpart for Markdown/offline HTML; callers still escape HTML."""
    if not isinstance(card,dict):return ''
    kind=card.get('kind')
    if kind=='quote':
        return (card.get('author') or '引用消息')+'：'+(card.get('quoted_text') or '引用内容未解析')
    if kind=='forwarded':
        lines=[(item.get('author') or '')+'：'+(item.get('text') or '[非文字消息]') for item in card.get('items',[])[:MAX_CARD_ITEMS]]
        if card.get('truncated'):lines.append('仅预览前 8 条转发消息')
        if card.get('item_count') is None:lines.append('转发详情未解析')
        return '\n'.join(lines)
    if kind=='file':return (card.get('extension') or '文件')+' · 本次未导出文件内容'
    if kind=='link':return (card.get('description') or '')+'\n'+(safe_web_url(card.get('url')) or '原链接缺失或不是可打开的网页地址')+'\n外部页面未自动加载'
    return ''
