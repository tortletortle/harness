"""Independent black-box acceptance; fixed expectations, never imports generated code."""
import csv
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

os.environ['EAS_REPAIR_EVIDENCE_DIR']='out/order-e2e-20260923'
sys.argv=[sys.argv[0],os.getenv('EAS_ORDER_CASE','case-1')]
import verify_live as a
import tempfile
temp_root=a.WS/'.tmp'
temp_root.mkdir(exist_ok=True)
for key in ('TMP','TEMP','TMPDIR'):os.environ[key]=str(temp_root)
tempfile.tempdir=str(temp_root)

SUFFIX='-'+os.getenv('EAS_ORDER_RECOVERY_TAG','retry') if os.getenv('EAS_ORDER_RECOVERED')=='1' else ''

FILES=('cleaned.csv','rejected.csv','duplicates.csv','customers.csv','summary.json','report.md')
HEADERS={'cleaned.csv':['order_id','customer','amount','status'],
    'rejected.csv':['row_number','order_id','reason'],'duplicates.csv':['row_number','order_id'],
    'customers.csv':['customer','paid_total','refunded_total','net_total','pending_total']}
A={
 'cleaned.csv':[['A001','Alice','120.50','paid'],['A002','Bob','80.00','paid'],['A003','Alice','25.50','refunded'],['A006','Bob','40.00','pending'],['A007','Carol','60.00','paid'],['A008','Alice','10.00','refunded'],['A010','Dan','0.00','paid']],
 'rejected.csv':[['6','A004','invalid_amount'],['7','A005','missing_customer'],['11','A009','negative_amount'],['13','A011','invalid_status']],
 'duplicates.csv':[['5','A002']],
 'customers.csv':[['Alice','120.50','35.50','85.00','0.00'],['Bob','80.00','0.00','80.00','40.00'],['Carol','60.00','0.00','60.00','0.00'],['Dan','0.00','0.00','0.00','0.00']],
 'summary.json':dict(input_rows=12,duplicate_rows=1,rejected_rows=4,valid_rows=7,paid_orders=4,refunded_orders=2,pending_orders=1,paid_total='260.50',refunded_total='35.50',net_total='225.00',pending_total='40.00')}
B_INPUT='''order_id,customer,amount,status
Z001,Zoë,0.10,paid
Z002,Zoë,0.20,paid
Z003,Zoë,0.10,refunded
Z004,李雷,5.00,pending
Z002,Zoë,0.20,paid
Z005,  ,9.99,paid
Z006,Max,NaN,paid
Z007,Max,Infinity,paid
Z008,Max,1.001,paid
,Max,1.00,paid
Z009,Max,1e2,paid
Z010,Max,-1.00,paid
Z006,Max,1.00,paid
Z011,Max,2.00,PAID
Z012, Max ,2,paid
'''
B={
 'cleaned.csv':[['Z001','Zoë','0.10','paid'],['Z002','Zoë','0.20','paid'],['Z003','Zoë','0.10','refunded'],['Z004','李雷','5.00','pending'],['Z012','Max','2.00','paid']],
 'rejected.csv':[['7','Z005','missing_customer'],['8','Z006','invalid_amount'],['9','Z007','invalid_amount'],['10','Z008','invalid_amount'],['11','','missing_order_id'],['12','Z009','invalid_amount'],['13','Z010','negative_amount'],['15','Z011','invalid_status']],
 'duplicates.csv':[['6','Z002'],['14','Z006']],
 'customers.csv':[['Max','2.00','0.00','2.00','0.00'],['Zoë','0.30','0.10','0.20','0.00'],['李雷','0.00','0.00','0.00','5.00']],
 'summary.json':dict(input_rows=15,duplicate_rows=2,rejected_rows=8,valid_rows=5,paid_orders=3,refunded_orders=1,pending_orders=1,paid_total='2.30',refunded_total='0.10',net_total='2.20',pending_total='5.00')}


def hashes(folder):
    return {name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in FILES}


def check_output(folder,expected):
    for name,header in HEADERS.items():
        with (folder/name).open(encoding='utf-8-sig',newline='') as f:
            actual=list(csv.reader(f))
        assert actual==[header]+expected[name],{'file':name,'expected':[header]+expected[name],'actual':actual}
    actual=json.loads((folder/'summary.json').read_text(encoding='utf-8-sig'))
    for key,value in expected['summary.json'].items():
        assert actual.get(key)==value and type(actual[key]) is type(value),{'key':key,'expected':value,'actual':actual.get(key)}
    report=(folder/'report.md').read_text(encoding='utf-8-sig')
    assert len(report.strip())>50,'Missing explanatory report'
    for key in ('paid_total','refunded_total','net_total','pending_total'):
        assert expected['summary.json'][key] in report,('Report missing amount',key)
    assert any('\u4e00'<=c<='\u9fff' for c in report),'Expected a Chinese report'
    return hashes(folder)


def run(argv):
    result=subprocess.run(argv,cwd=a.WS,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60,
        env={**os.environ,'PYTHONIOENCODING':'utf-8'})
    return {'returncode':result.returncode,'stdout':result.stdout[-5000:],'stderr':result.stderr[-3000:]}


def main():
    assert not (a.RUN/('oracle'+SUFFIX+'.json')).exists(),'Do not overwrite previous acceptance'
    checks=[]
    def check(name,operation):
        try:
            detail=operation()
            row={'case':name,'passed':True,'detail':detail}
        except Exception as exc:
            row={'case':name,'passed':False,'error_type':type(exc).__name__,'error':a.clean(exc)}
        checks.append(row)
        a.save('oracle'+SUFFIX+'.json',{'passed':all(c['passed'] for c in checks),'checks':checks})
    saved=json.loads((a.RUN/'generated.json').read_text(encoding='utf-8'))
    output=a.WS/'out/orders'
    def input_unchanged():
        digest=hashlib.sha256((a.WS/'data/orders.csv').read_bytes()).hexdigest()
        assert digest==saved['input_sha256']
        return digest
    check('input_unchanged_before_blackbox',input_unchanged)
    check('public_fixture_exact_output',lambda:check_output(output,A))
    def repeat():
        before=hashes(output)
        result=run([sys.executable,'reconcile.py','--input','data/orders.csv','--output','out/orders'])
        assert result['returncode']==0,result
        assert hashes(output)==before,'Output bytes changed on rerun'
        check_output(output,A)
        return result
    check('byte_stable_rerun',repeat)
    def hidden():
        path=a.WS/'data/oracle-unseen.csv'
        path.write_text(B_INPUT,encoding='utf-8',newline='')
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        result=run([sys.executable,'reconcile.py','--input','data/oracle-unseen.csv','--output','out/oracle-unseen'])
        assert result['returncode']==0,result
        result['output_hashes']=check_output(a.WS/'out/oracle-unseen',B)
        assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
        return result
    check('unseen_unicode_decimal_validation_fixture',hidden)
    def missing():
        before=hashes(output)
        result=run([sys.executable,'reconcile.py','--input','data/does-not-exist.csv','--output','out/orders'])
        assert result['returncode']!=0,'Missing input incorrectly succeeded'
        assert hashes(output)==before,'Missing input damaged previous outputs'
        return result
    check('missing_input_preserves_outputs',missing)
    def self_tests():
        result=run([sys.executable,'-m','pytest','-c','pytest.ini','test_reconcile.py','-q','--junitxml='+str(a.RUN/('generated-tests'+SUFFIX+'.xml'))])
        assert result['returncode']==0,result
        cases=list(ET.parse(a.RUN/('generated-tests'+SUFFIX+'.xml')).iter('testcase'))
        assert len(cases)>=4,len(cases)
        assert all(c.find('skipped') is None and c.find('failure') is None and c.find('error') is None for c in cases)
        return {**result,'count':len(cases)}
    check('generated_tests_at_least_four_pass',self_tests)
    check('input_unchanged_after_all_checks',input_unchanged)
    def lifecycle():
        data=json.loads((a.RUN/('execution'+SUFFIX+'.json')).read_text(encoding='utf-8'))
        assert data['plan']['status']=='Completed',data['plan']['status']
        assert data['plan']['steps'] and all(s['status']=='Success' for s in data['plan']['steps']),data['plan']['steps']
        runs=data['runs']['runs']
        latest=max(runs,key=lambda r:r['started_at']) if runs else None
        assert latest and latest['status']=='Completed',runs
        assert data['status'].get('current_execution') is None
        reports=json.loads((a.RUN/('report-verification'+SUFFIX+'.json')).read_text(encoding='utf-8'))
        assert reports and all(r['hash_valid'] is True for r in reports),reports
        artifacts=json.loads((a.RUN/('artifacts'+SUFFIX+'.json')).read_text(encoding='utf-8'))
        job=next(j for j in artifacts if j['kind']=='delivery_report_job' and j['run_id']==latest['run_id'])
        assert job['metadata']['status']=='completed'
        snapshot_steps=[s for segment in job['metadata']['snapshot']['segments'] for s in segment['steps']]
        assert snapshot_steps and all(s['status']=='Success' for s in snapshot_steps)
        assert any(j['kind']=='delivery_report' and j['run_id']==latest['run_id'] for j in artifacts)
        return {'logical_steps_success':len(data['plan']['steps']),'latest_report_snapshot_success':len(snapshot_steps),'plan':'Completed','runs':len(runs),'historical_failed_runs':sum(r['status']=='Failed' for r in runs),'reports':len(reports),'ownership_released':True}
    check('lifecycle_and_delivery_report',lifecycle)
    result={'passed':all(c['passed'] for c in checks),'checks':checks}
    a.save('oracle'+SUFFIX+'.json',result)
    print(a.clean(json.dumps(result,ensure_ascii=False,indent=2)),flush=True)
    return result


if __name__=='__main__':
    raise SystemExit(0 if main()['passed'] else 1)
