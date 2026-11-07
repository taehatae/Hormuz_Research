"""Offline-only regression evidence harness; never calls --execute."""
from pathlib import Path
import sys,json,os,unittest,io,contextlib,warnings,time
from unittest.mock import patch
import body_pilot_200 as p
ROOT=p.ROOT
RUN=ROOT/'hormuz_2026_data/runs/body_pilot_200_20261107_01'
EDITABLE={'scripts/body_pilot_200.py','scripts/test_body_pilot_200.py',str((RUN/'pilot_lock.json').relative_to(ROOT)).replace('\\','/'),str((RUN/'README.md').relative_to(ROOT)).replace('\\','/'),str((RUN/'PILOT_EXECUTION_PLAN.md').relative_to(ROOT)).replace('\\','/')}
def snapshot():
    target=RUN/'regression_before.json'
    if target.exists():raise FileExistsError('Baseline already exists; never replace')
    prior=p.jread(RUN/'preservation_before.json')['hashes']
    names=set(prior)|{x.relative_to(ROOT).as_posix() for x in RUN.iterdir() if x.is_file()}|{'scripts/body_pilot_200.py','scripts/test_body_pilot_200.py'}
    entries={}
    for n in sorted(names):
        f=ROOT/n
        if f.is_file():entries[n]=dict(sha256=p.sha(f),bytes=f.stat().st_size,mtime_ns=f.stat().st_mtime_ns,editable=n in EDITABLE)
    p.atomic_json(target,dict(files=entries,lock_before=p.jread(RUN/'pilot_lock.json'),network_requests=0))
    print(json.dumps(dict(baseline_files=len(entries)),indent=2))
def tests(label):
    token=p._NO_NETWORK.set(True);buf=io.StringIO();start=time.monotonic()
    prior_offline=p._OFFLINE_PROCESS;p._OFFLINE_PROCESS=True
    try:
        with warnings.catch_warnings(record=True) as caught,patch('socket.getaddrinfo',side_effect=AssertionError('real DNS forbidden')),patch('socket.create_connection',side_effect=AssertionError('real connection forbidden')):
            suite=unittest.defaultTestLoader.discover(str(ROOT/'scripts'),pattern='test_body_pilot_200*.py')
            result=unittest.TextTestRunner(stream=buf,verbosity=2).run(suite)
        (RUN/f'regression_{label}_tests.log').write_text(buf.getvalue(),encoding='utf-8')
        d=dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),passed=result.wasSuccessful(),elapsed_sec=round(time.monotonic()-start,3),real_network_requests=0,fixtures='synthetic only',warnings=[dict(category=w.category.__name__,message=str(w.message)) for w in caught])
        p.atomic_json(RUN/f'regression_{label}_tests.json',d);print(buf.getvalue());return d
    finally:p._NO_NETWORK.reset(token);p._OFFLINE_PROCESS=prior_offline
def finalize():
    lock=RUN/'pilot_lock.json';original=lock.read_bytes()
    try:return _finalize()
    except BaseException:
        if not lock.exists() or lock.read_bytes()!=original:p.atomic_bytes(lock,original)
        raise

def _finalize():
    d=tests('final')
    if not d['passed']:raise SystemExit('Do not update lock: tests failed')
    before=p.jread(RUN/'regression_before.json');lock=p.jread(RUN/'pilot_lock.json')
    assert lock==before['lock_before'],'Unexpected intervening lock change'
    for n,old in before['files'].items():
        if not old['editable']:
            f=ROOT/n
            assert p.sha(f)==old['sha256'] and f.stat().st_mtime_ns==old['mtime_ns'],'Protected file changed:'+n
    assert all(not any((RUN/n).iterdir()) for n in ['html','texts','results','robots']),'Unexpected real output files'
    for n,h in lock['immutable_hashes'].items():assert p.sha(RUN/n)==h
    lock['code_hashes']['scripts/body_pilot_200.py']=p.sha(ROOT/'scripts/body_pilot_200.py')
    p.atomic_json(RUN/'pilot_lock.json',lock)
    token=p._NO_NETWORK.set(True);buf=io.StringIO()
    try:
        with patch.object(sys,'argv',['body_pilot_200.py','--run-dir',str(RUN)]),contextlib.redirect_stdout(buf):p.main()
    except BaseException:
        p.atomic_json(RUN/'pilot_lock.json',before['lock_before'])
        raise
    finally:p._NO_NETWORK.reset(token)
    dry=json.loads(buf.getvalue());p.atomic_json(RUN/'regression_dry_run.json',dry)
    changes=[];after={}
    for n,old in before['files'].items():
        f=ROOT/n;now=dict(sha256=p.sha(f),bytes=f.stat().st_size,mtime_ns=f.stat().st_mtime_ns)
        if now['sha256']!=old['sha256'] or now['mtime_ns']!=old['mtime_ns']:changes.append(dict(path=n,authorized_edit=old['editable']))
        after[n]=now
    unexpected=[x for x in changes if not x['authorized_edit']]
    rows=p.StateStore(RUN,p.csvread(RUN/'pilot_200_queue.csv')).load()
    validation=dict(passed=not unexpected and dry['HTTP_requests']==0 and dry['URL_count']==200 and all(not any((RUN/n).iterdir()) for n in ['html','texts','results','robots']),tests=d,changes=changes,unexpected_changes=unexpected,
        unchanged_queue_and_state=all(after[str((RUN/n).relative_to(ROOT)).replace('\\','/')]['sha256']==before['files'][str((RUN/n).relative_to(ROOT)).replace('\\','/')]['sha256'] for n in ['pilot_200_queue.csv','pilot_state.csv','pilot_config.json']),
        URL_count=len(rows),states=dict(p.collections.Counter(r['status'] for r in rows)),lock_after=lock,
        actual_article_output_dirs_empty=all(not any((RUN/n).iterdir()) for n in ['html','texts','results','robots']),actual_HTTP_DNS_robots_API_requests=0,actual_article_extractions=0)
    if not validation['passed'] or not validation['unchanged_queue_and_state']:
        p.atomic_json(RUN/'pilot_lock.json',before['lock_before']);validation['lock_rolled_back']=True;validation['lock_after']=before['lock_before']
        p.atomic_json(RUN/'offline_regression_validation.json',validation)
        raise AssertionError('Post-lock validation failed; original lock restored')
    p.atomic_json(RUN/'regression_after.json',dict(files=after));p.atomic_json(RUN/'offline_regression_validation.json',validation)
    projected=p.article_outcomes(RUN,p.csvread(RUN/'pilot_200_queue.csv'))
    for r in projected:
        for k,v in list(r.items()):
            if isinstance(v,list):r[k]=json.dumps(v,ensure_ascii=False)
    p.csvwrite(RUN/'offline_initial_outcomes.csv',projected)
    p.atomic_json(RUN/'offline_initial_summary.json',p.summarize_run(RUN,p.csvread(RUN/'pilot_200_queue.csv')))
    hashrows=[dict(path=n,before_sha256=before['files'][n]['sha256'],after_sha256=x['sha256'],changed=x['sha256']!=before['files'][n]['sha256']) for n,x in after.items()]
    p.csvwrite(RUN/'regression_hash_comparison.csv',hashrows)
    print(json.dumps(validation,indent=2));assert validation['passed'] and validation['unchanged_queue_and_state']
if __name__=='__main__':
    mode=sys.argv[1] if len(sys.argv)>1 else 'tests'
    if mode=='snapshot':snapshot()
    elif mode=='finalize':finalize()
    elif mode in {'red','tests'}:
        if not tests(mode)['passed']:raise SystemExit(1)
    else:raise SystemExit('snapshot | red | tests | finalize')
