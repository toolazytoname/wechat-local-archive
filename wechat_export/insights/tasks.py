"""Archive-bound background profile tasks. No automatic replay of cloud calls."""
from __future__ import annotations
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from .store import open_store, InsightsError
from .profile_pipeline import run_profile
from ..jobs import JobStore


def context_hash(store):
    rows={table:[list(row) for row in store.conn.execute(f'SELECT * FROM {table} ORDER BY 1')]
          for table in ('identity','conversation_roles','people','corrections')}
    return hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()


def public_task(job):
    p=job.payload
    return {'job_id':job.job_id,'state':job.state,'created_at':job.created_at,'phase':p.get('phase'),
            'progress':p.get('progress',0),'run_id':p.get('run_id'),'error':job.error,
            'kind':p.get('profile_kind'),'summary_id':p.get('summary_id'),'item_id':p.get('item_id'),'remote':bool(p.get('remote')),
            'can_restart':job.state in {'failed','blocked','cancelled'} and not p.get('remote'),
            'note':'取消会停止后续处理，但已发出的模型请求无法撤回。' if p.get('remote') else ''}


def bound_task(jobs, job_id, binding):
    job=jobs.get(job_id)
    if job is None or job.kind!='profile_analysis' or job.payload.get('archive_root')!=str(binding.root):
        raise InsightsError('Task not found','not_found')
    return job


class CheckedProvider:
    def __init__(self, provider, expected, check):
        self.provider=provider;self.expected=expected;self.check=check;self.kind=provider.kind
    def analyze(self,payload):
        self.check()
        if payload.get('records')!=self.expected:
            raise InsightsError('Approved input changed; preview again','scope_changed')
        result=self.provider.analyze(payload)
        self.check()
        return result


def start_profile_task(jobs: JobStore, data_root: Path, binding, *, request: dict,
                       provider, approved_records=None) -> dict:
    binding.verify()
    s=open_store(data_root,binding.revision,archive_root=binding.root)
    try:fingerprint=context_hash(s)
    finally:s.close()
    remote=getattr(provider,'kind','') in {'remote','grok_cli'}
    job=jobs.create('profile_analysis','queued',{
        'archive_root':str(binding.root),'source_revision':binding.revision,'profile_kind':request.get('kind','self'),
        'scope':request.get('scope') or {},'subject_person_id':request.get('subject_person_id'),
        'remote':remote,'engine_id':provider.engine_id,'phase':'等待开始','progress':0})
    def worker():
        started=time.monotonic();conn=None;store=None
        def check():
            if jobs.cancelled(job.job_id):raise InsightsError('Task cancelled','cancelled')
            if time.monotonic()-started>900:raise InsightsError('Task time limit reached','task_timeout')
            binding.verify()
            if store is not None and context_hash(store)!=fingerprint:
                raise InsightsError('Identity or exclusions changed; start again','context_changed')
        try:
            check();job.state='running';job.payload.update(phase='读取本机资料',progress=10);jobs.save(job)
            store=open_store(data_root,binding.revision,archive_root=binding.root);check()
            conn=sqlite3.connect(binding.index_path.as_uri()+'?mode=ro',uri=True);conn.row_factory=sqlite3.Row
            conn.set_progress_handler(lambda:1 if jobs.cancelled(job.job_id) else 0,1000)
            wrapped=CheckedProvider(provider,approved_records,check) if remote else provider
            job.payload.update(phase='等待模型返回' if remote else '整理完整原话',progress=30);jobs.save(job)
            result=run_profile(store,conn,kind=request.get('kind','self'),source_revision=binding.revision,
                scope=request.get('scope') or {},subject_person_id=request.get('subject_person_id'),
                engine_id=provider.engine_id,provider=wrapped,before_publish=check)
            job.state='ready';job.payload.update(run_id=result['run_id'],phase='报告已保存',progress=100);jobs.save(job)
        except Exception as exc:
            job.state='cancelled' if jobs.cancelled(job.job_id) else 'failed'
            code=getattr(exc,'code','analysis_failed')
            job.error={'code':code,'message':'任务未完成。资料未改动；请检查范围、身份或引擎后重新开始。'}
            jobs.save(job)
        finally:
            if conn is not None:conn.close()
            if store is not None:store.close()
    jobs.run_in_thread(job.job_id,worker)
    return public_task(job)


def start_learning_task(jobs,data_root,binding,*,item_id,provider,prepared):
    from ..learning.semantic import generate_learning
    job=jobs.create('profile_analysis','queued',{'archive_root':str(binding.root),'source_revision':binding.revision,
        'profile_kind':'learning','item_id':item_id,'remote':True,'phase':'等待整理文章','progress':0})
    def worker():
        s=None;started=time.monotonic()
        def check():
            if time.monotonic()-started>900:raise InsightsError("Task time limit reached","task_timeout")
            if jobs.cancelled(job.job_id):raise InsightsError('Cancelled','cancelled')
            binding.verify()
        def progress(done,total):
            job.payload.update(progress=int(done/total*90),phase=f'已整理 {done}/{total} 部分');jobs.save(job)
        try:
            check();job.state='running';jobs.save(job)
            s=open_store(data_root,binding.revision,archive_root=binding.root)
            result=generate_learning(s,item_id,provider,prepared,check,progress)
            job.state='ready';job.payload.update(summary_id=result['summary_id'],progress=100,phase='学习草稿已保存');jobs.save(job)
        except Exception as exc:
            job.state='cancelled' if jobs.cancelled(job.job_id) else 'failed'
            job.error={'code':getattr(exc,'code','summary_failed'),'message':'整理未完成。正文未改动，请检查引擎或重新预览。'};jobs.save(job)
        finally:
            if s is not None:s.close()
    jobs.run_in_thread(job.job_id,worker)
    return public_task(job)
