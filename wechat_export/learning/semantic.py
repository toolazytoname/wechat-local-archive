"""Explicitly approved article synthesis. Citations verified; meaning stays a draft."""
import hashlib
import json
import uuid
from .importer import get_item
from ..insights.store import InsightsError, utc_now

MAX_CHARS=200000
CHUNK_CHARS=12000


def prepare_learning(store,item_id):
    item=get_item(store,item_id)
    if not item['contents'] or not item['contents'][-1].get('body'):
        raise InsightsError('请先添加文章正文。','title_only')
    content=item['contents'][-1];body=content['body']
    if len(body)>MAX_CHARS:raise InsightsError('文章超过20万字，请拆成几篇后整理。','content_too_large')
    paragraphs=[]
    for i,p in enumerate([p for p in body.splitlines() if p.strip()] or [body],1):
        for start in range(0,len(p),CHUNK_CHARS):
            paragraphs.append({'paragraph_id':f'p{i}-{start//CHUNK_CHARS+1}','text':p[start:start+CHUNK_CHARS]})
    chunks=[];current=[];size=0
    for p in paragraphs:
        if current and size+len(p['text'])>CHUNK_CHARS:chunks.append(current);current=[];size=0
        current.append(p);size+=len(p['text'])
    if current:chunks.append(current)
    digest=hashlib.sha256(body.encode()).hexdigest()
    if digest!=content['body_hash']:raise InsightsError('正文摘要不一致，请先恢复正文。','invalid_content')
    return {'item_id':item_id,'content_id':content['content_id'],'body_hash':digest,
            'paragraphs':paragraphs,'chunks':chunks,'chars':len(body),'request_count':len(chunks)}


def validate_learning_result(raw, paragraphs):
    records={p['paragraph_id']:p['text'] for p in paragraphs}
    claims=[];questions=[]
    for field,out in [('claims',claims),('questions',questions)]:
        values=raw.get(field,[]) if isinstance(raw,dict) else []
        if not isinstance(values,list):continue
        for value in values[:12]:
            if not isinstance(value,dict):continue
            ids=value.get('paragraph_ids');quote=value.get('quote')
            if not isinstance(ids,list) or not ids or not all(isinstance(i,str) and i in records for i in ids):continue
            if not isinstance(quote,str) or not quote.strip() or not any(quote in records[i] for i in ids):continue
            names=['text'] if field=='claims' else ['question','answer']
            if not all(isinstance(value.get(k),str) and value[k].strip() and len(value[k])<=4000 for k in names):continue
            out.append({**{k:value[k] for k in names},'paragraph_ids':ids,'quote':quote,
                        'origin':'model_draft','verification':'citation_checked_meaning_unverified'})
    return claims,questions


def generate_learning(store,item_id,provider,prepared,check=lambda:None,progress=lambda done,total:None):
    claims=[];questions=[]
    for i,chunk in enumerate(prepared['chunks']):
        check()
        current=prepare_learning(store,item_id)
        if current['content_id']!=prepared['content_id'] or current['body_hash']!=prepared['body_hash']:
            raise InsightsError('正文已更新，请重新预览并批准。','content_changed')
        raw=provider.summarize({'paragraphs':chunk})
        check();cs,qs=validate_learning_result(raw,chunk);claims.extend(cs);questions.extend(qs)
        progress(i+1,len(prepared['chunks']))
    current=prepare_learning(store,item_id)
    if current['content_id']!=prepared['content_id'] or current['body_hash']!=prepared['body_hash']:
        raise InsightsError('正文已更新，本次旧版本结果未发布。','content_changed')
    if not claims:raise InsightsError('模型结果没有通过来源核对。','invalid_summary')
    summary_id='sum_'+uuid.uuid4().hex
    try:
        check()
        store.conn.execute('INSERT INTO learning_summaries(summary_id,item_id,content_id,engine_id,claims_json,status,created_at) VALUES (?,?,?,?,?,?,?)',
            (summary_id,item_id,prepared['content_id'],provider.engine_id,json.dumps(claims,ensure_ascii=False),'draft',utc_now()))
        for q in questions:
            store.conn.execute('INSERT INTO review_cards(card_id,item_id,content_id,question,answer,citation_json,user_state) VALUES (?,?,?,?,?,?,?)',
                ('card_'+uuid.uuid4().hex,item_id,prepared['content_id'],q['question'],q['answer'],json.dumps({'paragraph_ids':q['paragraph_ids'],'quote':q['quote'],'origin':'model_draft'},ensure_ascii=False),'draft'))
        check();store.conn.commit()
    except Exception:store.conn.rollback();raise
    return {'summary_id':summary_id,'status':'draft','claims':claims,'question_count':len(questions),
            'note':'AI整理草稿：引文已核对，归纳含义仍需你核对。'}
