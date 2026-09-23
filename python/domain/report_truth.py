"""Frozen, bounded report evidence; absence of an audit record is not a negative fact."""
from collections import Counter
from contextlib import closing
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import re
import unicodedata

report_facts = ContextVar('report_execution_facts', default=None)


class ReportTruthError(ValueError):
    """A report makes a prohibited or numerically contradictory assertion."""


def unknown_facts(plan):
    return {'schema_version': 1, 'plan_id': str(plan.plan_id), 'run_id': None,
            'history_status': 'unknown', 'run_counts': None, 'runs_total': None,
            'current_run_attempt_counts': None, 'current_run_attempts_total': None,
            'runs_sample': [], 'runs_sample_truncated': False,
            'current_branch': plan.current_branch,
            'current_steps': dict(Counter(s.status.value for g in plan.segments for s in g.steps)),
            'manual_interventions': {'status': 'unknown', 'records': []}}


def capture_facts(database, plan, run_id=None, interventions=None):
    """One SQLite read snapshot, exact aggregate counts, at most 40 Run details.

    Operator notes are explicitly attributed declarations, not independently
    proven facts. No owner tokens, exception messages or arbitrary paths escape.
    """
    notes=[]
    if interventions is not None:
        if not isinstance(interventions, list) or len(interventions)>20:
            raise ValueError('Invalid intervention records')
        for entry in interventions:
            if not isinstance(entry, dict) or set(entry)!={'description','evidence_sha256'}:
                raise ValueError('Invalid intervention record fields')
            desc,sha=entry['description'],entry['evidence_sha256']
            if not isinstance(desc,str) or not desc.strip() or len(desc)>500:
                raise ValueError('Invalid intervention description')
            if not isinstance(sha,str) or not re.fullmatch(r'[0-9a-f]{64}',sha):
                raise ValueError('Invalid intervention evidence hash')
            notes.append({'description':desc,'evidence_sha256':sha})
    facts=unknown_facts(plan)
    with closing(database._get_connection()) as conn:
        conn.execute('BEGIN')
        cutoff=conn.execute('SELECT COALESCE(MAX(rowid),0) FROM execution_runs WHERE plan_id=?',(str(plan.plan_id),)).fetchone()[0]
        if run_id:
            row=conn.execute('SELECT rowid FROM execution_runs WHERE plan_id=? AND run_id=?',(str(plan.plan_id),str(run_id))).fetchone()
            if row is None:
                raise ValueError('Report Run does not belong to this plan')
            cutoff=row[0]
        args=(str(plan.plan_id),cutoff)
        counts={r['status']:r['n'] for r in conn.execute(
            'SELECT status,COUNT(*) AS n FROM execution_runs WHERE plan_id=? AND rowid<=? GROUP BY status',args)}
        rows=[dict(r) for r in conn.execute(
            'SELECT run_id,status,branch_version,trigger,started_at,finished_at FROM execution_runs WHERE plan_id=? AND rowid<=? ORDER BY rowid DESC LIMIT 40',args)]
        attempts=({r['status']:r['n'] for r in conn.execute(
            'SELECT status,COUNT(*) AS n FROM execution_attempts WHERE run_id=? GROUP BY status',(str(run_id),))}
            if run_id else None)
    facts.update(current_run_attempt_counts=attempts,current_run_attempts_total=sum(attempts.values()) if attempts is not None else None,
                 history_status='recorded',run_id=str(run_id) if run_id else None,
                 captured_at=datetime.now(timezone.utc).isoformat(),run_counts=counts,
                 runs_total=sum(counts.values()),runs_sample=rows,
                 runs_sample_truncated=sum(counts.values())>len(rows))
    if notes:
        facts['manual_interventions']={'status':'operator_recorded','records':notes}
    return facts


def validate_narrative(text, facts):
    """Conservative lexical/numeric guard, NOT a general semantic verifier.

    Even a single recorded Run cannot establish that no unrecorded intervention
    took place. Do not accept unsupported global success/production claims.
    """
    normalized=unicodedata.normalize('NFKC',str(text)).lower()
    normalized=''.join(c for c in normalized if unicodedata.category(c)!='Cf')
    compact=re.sub(r'\s+','',normalized)
    prohibited=('无失败','无重试','零人工','没有人工干预','无需人工干预','首次成功',
                '一次成功','一遍通过','全程顺利','可直接投产','生产就绪',
                'nofailures','noretries','nomanualintervention','zeromanualintervention',
                'withouthumanintervention','firstattempt','firsttry','productionready')
    if any(p in compact for p in prohibited):
        raise ReportTruthError('Unsupported global assertion in model report')
    if facts.get('current_run_attempts_total')==0 and re.search(
            r'本次(?:运行|run)(?:的)?[1-9一二三四五六七八九][个项步]*步骤',compact):
        raise ReportTruthError('Zero-attempt Run described as executing snapshot steps')
    counts=facts.get('run_counts')
    for match in re.finditer(r'(?:历史失败(?:run|运行)?(?:次数)?|historicalfailedruns)[:=为共]*(\d+)',compact):
        if counts is None or int(match[1])!=counts.get('Failed',0):
            raise ReportTruthError('Historical failure count contradicts frozen evidence')


def render_report(summary, facts):
    validate_narrative(summary,facts)
    counts=facts.get('run_counts')
    failed=str(counts.get('Failed',0)) if counts is not None else '未知'
    total=str(facts.get('runs_total')) if counts is not None else '未知'
    manual=facts['manual_interventions']
    note=('已登记操作方说明 '+str(len(manual['records']))+' 条；声明不等于独立证明。'
          if manual['status']=='operator_recorded' else '未知；未登记不等于未发生。')
    encoded=json.dumps(facts,ensure_ascii=False,indent=2)
    fence='`'*max(3,1+max((len(m[0]) for m in re.finditer(r'`+',encoded)),default=0))
    return ('# 交付报告\n\n## 系统核验事实（冻结快照）\n\n'
            f'- 已记录 Run 总数：{total}\n- 历史 Failed Run：{failed}\n'
            f'- 关联 Run 的 Attempt 记录数：{facts.get("current_run_attempts_total") if facts.get("current_run_attempts_total") is not None else "未知"}\n'
            f'- 当前分支：{facts["current_branch"]}\n- 当前逻辑步骤：{json.dumps(facts["current_steps"],ensure_ascii=False)}\n'
            f'- 人工/操作方干预：{note}\n'
            '- 范围：仅截至此快照的本计划数据库记录；不含未记录操作或设计阶段调用错误。\n'
            '- 当前步骤成功不代表整个历史没有失败，也不证明关联 Run 重新执行了这些步骤；通过测试不构成生产认证。\n\n'
            f'{fence}json\n{encoded}\n{fence}\n\n'
            '## 模型补充说明（不替代独立验收）\n\n'+str(summary).strip()+'\n')
