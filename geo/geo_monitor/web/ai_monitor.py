"""Explicitly approved, bounded API jobs. Never consumes the legacy scheduler queue."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
import httpx

from ..analyzer import analyze
from ..config import MARKET_COUNTRY
from ..db import DB
from ..engines.openai_engine import parse_response
from ..questions import load_questions

MODEL = 'gpt-5.4-mini'
PRICE_VERSION = '2026-10-25'
INPUT_PRICE, OUTPUT_PRICE, SEARCH_PRICE = .75, 4.50, .01
# A planning allowance, not a provider-enforced monetary cap.
ALLOWANCE = .10
MAX_QUESTIONS = 5
DAILY_ALLOWANCE = 1.0
STATES = {'prepared':'待确认','queued':'等待执行','running':'检测中','done':'已完成',
          'partial':'部分完成','failed':'失败','interrupted':'已中断，未自动重试','cancelled':'已取消'}


def now():
    return datetime.now(timezone.utc).isoformat()


class AIError(ValueError):
    pass


def safe_url(value):
    try:
        p = urlsplit(value)
        return value if p.scheme in {'http','https'} and p.hostname and not p.username and not p.password else ''
    except (ValueError, TypeError):
        return ''


class AIMonitor:
    def __init__(self, cfg, transport=None):
        self.cfg, self.transport = cfg, transport
        self.folder = Path(os.environ.get('AI_DATA_DIR','data/ai-monitor'))
        self.private = Path(os.environ.get('AI_PRIVATE_DIR','data/.ai-private'))
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.pending = {}

    @property
    def enabled(self):
        return os.environ.get('WEB_AI_PAID_ENABLED','false').lower() == 'true'

    @contextmanager
    def database(self):
        self.folder.mkdir(parents=True,exist_ok=True)
        db = sqlite3.connect(self.folder/'monitor.db',timeout=20)
        db.row_factory = sqlite3.Row
        try:
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, finished_at TEXT,
                status TEXT NOT NULL, run_id INTEGER, questions TEXT NOT NULL,
                allowance REAL NOT NULL, consent TEXT NOT NULL, usage TEXT NOT NULL DEFAULT '[]',
                error TEXT NOT NULL DEFAULT '')''')
            with db:
                yield db
        finally:
            db.close()

    def key(self):
        p = self.private/'openai.enc'
        if not p.exists():
            return ''
        try:
            return Fernet((self.private/'encryption.key').read_bytes()).decrypt(p.read_bytes()).decode()
        except (OSError,InvalidToken,ValueError):
            raise AIError('密钥文件无法解密，请保留原加密文件与备份，再重新配置。') from None

    def save_key(self, value):
        value = value.strip()
        if not value:
            return
        if not re.fullmatch(r'sk-[A-Za-z0-9_-]{20,250}',value):
            raise AIError('请填写完整的 OpenAI API 密钥，不要填写 ChatGPT 登录密码。')
        self.private.mkdir(parents=True,exist_ok=True,mode=0o700)
        k = self.private/'encryption.key'
        if not k.exists():
            with k.open('xb') as f:
                f.write(Fernet.generate_key())
        tmp = self.private/'openai.tmp'
        tmp.write_bytes(Fernet(k.read_bytes()).encrypt(value.encode()))
        tmp.chmod(0o600)
        tmp.replace(self.private/'openai.enc')
        k.chmod(0o600)

    def disconnect(self):
        # Empty encrypted content retains the original encryption key and job history.
        with self.lock:
            p = self.private/'openai.enc'
            if p.exists():
                tmp = self.private/'openai.tmp'
                tmp.write_bytes(Fernet((self.private/'encryption.key').read_bytes()).encrypt(b''))
                tmp.chmod(0o600); tmp.replace(p)

    def questions(self):
        try:
            return [asdict(q) for q in load_questions(self.cfg.questions_file)]
        except (ValueError,OSError,KeyError):
            raise AIError('无法读取监测问题文件，请检查原有 questions.xlsx；历史记录仍保留。') from None

    def history(self, limit=100):
        with self.database() as db:
            sql='SELECT * FROM jobs ORDER BY id DESC'+(' LIMIT ?' if limit else '')
            rows = [dict(r) for r in db.execute(sql,(limit,) if limit else ())]
        for r in rows:
            r['questions'] = json.loads(r['questions']); r['usage'] = json.loads(r['usage'])
            r['cost'] = sum(u.get('estimated_usd',0) for u in r['usage'])
        return rows

    def summary(self):
        try:
            configured, error = bool(self.key()), ''
        except AIError as exc:
            configured, error = False, str(exc)
        jobs = self.history()
        return dict(configured=configured,enabled=self.enabled,error=error,jobs=jobs,
                    completed=sum(j['status'] in {'done','partial'} for j in jobs),
                    model=MODEL,allowance=ALLOWANCE,max_questions=MAX_QUESTIONS,
                    daily_allowance=DAILY_ALLOWANCE,price_version=PRICE_VERSION)

    def prepare(self, sid, ids):
        if not self.enabled:
            raise AIError('付费调用尚未开启。可先配置密钥、查看问题，确认费用后再启用。')
        if not self.key():
            raise AIError('请先保存 API 密钥；保存密钥不会自动调用接口。')
        if not ids or len(ids) > MAX_QUESTIONS or len(set(ids)) != len(ids):
            raise AIError(f'每次请选择 1–{MAX_QUESTIONS} 个不同问题。')
        available = {q['id']:q for q in self.questions()}
        if any(i not in available for i in ids):
            raise AIError('问题列表已变化，请刷新后重新选择。')
        qs = [available[i] for i in ids]
        if any(len(q['prompt'])>2000 for q in qs):
            raise AIError('单个监测问题不能超过 2000 字符。')
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.pending = {k:v for k,v in self.pending.items() if v['expires'] > datetime.now().timestamp()}
            self.pending[token] = dict(sid=sid,questions=qs,expires=datetime.now().timestamp()+600,
                                       key_digest=hashlib.sha256(self.key().encode()).hexdigest())
        return token, qs

    def approve(self, sid, token, consent):
        with self.lock:
            item = self.pending.get(token)
            if (not item or item['sid'] != sid or item['expires'] < datetime.now().timestamp()
                    or consent != 'yes' or not self.enabled):
                raise AIError('请重新预览任务，并勾选本次费用与数据发送确认。')
            if not self.key() or hashlib.sha256(self.key().encode()).hexdigest() != item['key_digest']:
                raise AIError('密钥已变化，请重新预览费用。')
            amount = round(len(item['questions'])*ALLOWANCE,2)
            with self.database() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running')").fetchone():
                    raise AIError('已有任务正在等待或执行，完成后再提交。')
                day = datetime.now(timezone.utc).date().isoformat()
                reserved = db.execute("SELECT COALESCE(SUM(allowance),0) FROM jobs WHERE created_at>=?",(day,)).fetchone()[0]
                if reserved+amount > DAILY_ALLOWANCE+.00001:
                    raise AIError('今日任务预留额度已满（UTC 日限 $1），请明天再试。')
                cur = db.execute('INSERT INTO jobs(created_at,status,questions,allowance,consent) VALUES(?,?,?,?,?)',
                    (now(),'queued',json.dumps(item['questions'],ensure_ascii=False),amount,
                     json.dumps(dict(model=MODEL,price_version=PRICE_VERSION,approved_at=now(),
                                     per_question=ALLOWANCE,automatic_schedule=False))))
                job = cur.lastrowid
            self.pending.pop(token,None)
        return job

    def api_call(self, key, q):
        tool = dict(type='web_search',search_context_size='low')
        country = MARKET_COUNTRY.get(q['market'])
        if country:
            tool['user_location'] = dict(type='approximate',country=country)
        body = dict(model=MODEL,input=q['prompt'],tools=[tool],tool_choice='required',
                    include=['web_search_call.action.sources'],max_tool_calls=1,max_output_tokens=1800,
                    reasoning={'effort':'none'},store=False)
        # One request only. An ambiguous network failure must never be retried automatically.
        with httpx.Client(timeout=120,transport=self.transport,follow_redirects=False) as client:
            try:
                response = client.post('https://api.openai.com/v1/responses',json=body,
                    headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
            except httpx.HTTPError:
                raise AIError('网络中断，未自动重试；本次可能已计费，请先核对 OpenAI 用量。') from None
        if response.status_code != 200:
            code = response.status_code
            explanation = {401:'密钥无效',403:'当前账号或地区无访问权限',429:'余额不足或触及速率限制'}.get(code,'服务暂不可用')
            raise AIError(f'OpenAI HTTP {code}：{explanation}；未自动重试。')
        try:
            data = response.json()
            res = parse_response(data,MODEL)
            receipt = dict(response_id=data.get('id',''),request_id=response.headers.get('x-request-id',''),
                input_tokens=res.input_tokens,output_tokens=res.output_tokens,search_calls=res.search_calls,
                model=res.model,provider_status=data.get('status','unknown'),
                citations=[dict(url=c.url,title=c.title) for c in res.citations if safe_url(c.url)],
                estimated_usd=round(res.input_tokens*INPUT_PRICE/1e6+res.output_tokens*OUTPUT_PRICE/1e6+res.search_calls*SEARCH_PRICE,6))
            return res, receipt
        except (ValueError,TypeError,KeyError):
            raise AIError('接口返回格式异常，未自动重试；请核对服务商用量。') from None

    def execute_one(self):
        with self.database() as jobs:
            jobs.execute('BEGIN IMMEDIATE')
            row = jobs.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if not row:
                return False
            job = dict(row)
            if not self.enabled:
                jobs.execute("UPDATE jobs SET status='cancelled',finished_at=?,error=? WHERE id=?",(now(),'付费调用开关关闭，任务未执行。',job['id']))
                return True
            jobs.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
        db = DB(self.cfg.database_url)
        receipts, run_id, error = [], None, ''
        try:
            key = self.key()
            if not key:
                raise AIError('API 密钥未配置，任务未执行。')
            questions = json.loads(job['questions'])
            run_id = db.insert('runs',dict(started_at=now(),status='running',engines='openai',
                priorities=','.join(sorted({q['priority'] for q in questions})),question_count=len(questions),
                note=f'growth-v2 API job #{job["id"]}; explicit consent; {PRICE_VERSION}'))
            with self.database() as jobs:
                jobs.execute('UPDATE jobs SET run_id=? WHERE id=?',(run_id,job['id']))
            for q in questions:
                if self.stop.is_set() or not self.enabled:
                    raise AIError('服务停止，剩余问题未执行。')
                res, receipt = self.api_call(key,q)
                receipts.append(receipt)
                # Persist usage even if analysis or result writing fails.
                with self.database() as jobs:
                    jobs.execute('UPDATE jobs SET usage=? WHERE id=?',(json.dumps(receipts),job['id']))
                if not res.text or receipt['provider_status'] != 'completed' or not res.search_calls:
                    raise AIError('回答不完整或未完成联网检索，不计作有效监测；用量记录已保留。')
                # Consulted sources are not citations in the actual answer.
                evidence=replace(res,sources=[])
                a = analyze(evidence,self.cfg.target_domain,self.cfg.brand_terms,self.cfg.ignore_domains,q['expected_url'])
                db.insert('results',dict(run_id=run_id,checked_at=now(),question_id=q['id'],engine='openai',
                    model=res.model,language=q['language'],market=q['market'],category=q['category'],priority=q['priority'],
                    prompt=q['prompt'],expected_url=q['expected_url'],mentioned=a.mentioned,cited=a.cited,
                    brand_in_text=a.brand_in_text,rank=a.rank,expected_hit=a.expected_hit,target_urls=a.target_urls,
                    competitors=a.competitors,cited_domains=a.cited_domains,search_queries=res.search_queries,
                    answer_text=res.text,input_tokens=res.input_tokens,output_tokens=res.output_tokens,search_calls=res.search_calls))
        except Exception as exc:
            error = str(exc) if isinstance(exc,AIError) else '任务处理异常，已停止且未自动重试；历史数据保留。'
        finally:
            count = db.query('SELECT COUNT(*) AS n FROM results WHERE run_id=%s',(run_id,))[0]['n'] if run_id else 0
            status = 'partial' if error and count else 'failed' if error else 'done'
            if run_id:
                db.execute('UPDATE runs SET status=%s,finished_at=%s WHERE id=%s',('done' if count else 'failed',now(),run_id))
            db.close()
            with self.database() as jobs:
                jobs.execute('UPDATE jobs SET status=?,finished_at=?,error=? WHERE id=?',(status,now(),error,job['id']))
        return True

    def start(self):
        with self.database() as db:
            db.execute("UPDATE jobs SET status='interrupted',finished_at=?,error=? WHERE status='running'",
                (now(),'服务曾中断。为避免重复计费，不自动重试；请核对已保存结果和 OpenAI 用量。'))
        self.stop.clear()
        def loop():
            while not self.stop.is_set():
                try:
                    self.execute_one()
                except Exception:
                    # No raw errors/credentials in logs; persistent running rows are recovered on restart.
                    pass
                self.stop.wait(2)
        self.thread = threading.Thread(target=loop,daemon=True,name='approved-ai-jobs')
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1)
