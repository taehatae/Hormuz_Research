"""Execute only the exact preflight-approved subset, with verified offline recovery."""
from pathlib import Path
import csv,json,hashlib,os,sys,subprocess,zipfile,datetime,collections
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent
VENDOR=(ROOT/'hormuz_2026_data/body_collection_pilot_20261102_01/vendor').resolve()
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()
def dump(n,v):(OUT/n).write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
def probe():
    q=subprocess.run([sys.executable,'-B',str(OUT/'cleanup_probe.py')],cwd=ROOT,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8'),capture_output=True,text=True,encoding='utf-8',timeout=60)
    if q.returncode:raise RuntimeError(q.stderr)
    return json.loads(q.stdout)
def ledger(rows):
    with (OUT/'deletion_execution_ledger.csv').open('w',encoding='utf-8-sig',newline='') as f:
        fields=['path','expected_size','expected_sha256','actual_sha256','precheck_match','decision','action','reason','backup_archive','backup_member','deleted_bytes','timestamp_utc','direct_project_refs','runtime_import_refs','resource_refs']
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for r in rows:
            q={k:r.get(k,'') for k in fields}
            for k in ['direct_project_refs','runtime_import_refs','resource_refs']:q[k]=';'.join(q[k]) if isinstance(q[k],list) else q[k]
            w.writerow(q)
def main():
    if (OUT/'deletion_execution_ledger.csv').exists():raise RuntimeError('Execution ledger already exists; inspect rather than replay')
    pre=json.loads((OUT/'cleanup_preflight.json').read_text(encoding='utf-8'));rows=pre['decisions']
    with (OUT/'deletion_candidates.csv').open(encoding='utf-8-sig',newline='') as f:allowed={r['current_relative_path']:r for r in csv.DictReader(f)}
    before=probe();dump('import_probe_execution_before.json',before)
    if not before['all_ok']:raise RuntimeError('Pre-deletion functionality probe failed; no files deleted')
    used={str(Path(p).resolve()).casefold() for p in before['candidate_files_read']+before['loaded_vendor_files']}
    dynamic=json.loads((OUT/'runtime_reference_details.json').read_text(encoding='utf-8'))['dynamic_resource_calls']
    candmods={r['path'].split('/vendor/',1)[-1][:-3].replace('/','.') for r in rows if '/vendor/' in r['path'] and r['path'].endswith('.py')}
    for r in rows:
        r.update(action='preserved',reason=';'.join(r['reasons']),deleted_bytes=0)
        if r['decision']!='eligible':continue
        p=(ROOT/r['path']).resolve()
        if p.is_symlink() or not p.is_relative_to(VENDOR) or r['path'] not in allowed:raise RuntimeError('Path escaped authorized vendor file set')
        if p.stat().st_size!=r['expected_size'] or sha(p)!=r['expected_sha256']:
            r.update(decision='hold',reason='immediate_size_or_SHA256_mismatch');continue
        if str(p).casefold() in used:
            r.update(decision='hold',reason='candidate_loaded_or_read_by_functionality_probe');continue
        vp=r['path'].split('/vendor/',1)[1];module=vp[:-3].replace('/','.') if vp.endswith('.py') else ''
        hits=[]
        for call in dynamic:
            if call['source'] in candmods:continue
            if any((module and (s==module or s.startswith(module+'.'))) or vp in s for s in call['literals']):hits.append(call['source'])
        if hits:r.update(decision='hold',reason='literal_dynamic_runtime_resource_reference:'+','.join(sorted(set(hits))))
    selected=[r for r in rows if r['decision']=='eligible']
    backup=OUT/'vendor_cleanup_recovery.zip'
    if backup.exists():raise RuntimeError('Backup already exists; do not overwrite')
    with zipfile.ZipFile(backup,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for r in selected:z.write(ROOT/r['path'],r['path'])
    with zipfile.ZipFile(backup) as z:
        if z.testzip() is not None:raise RuntimeError('Recovery archive CRC failure')
        for r in selected:
            data=z.read(r['path'])
            if len(data)!=r['expected_size'] or hashlib.sha256(data).hexdigest()!=r['expected_sha256']:raise RuntimeError('Recovery archive SHA256 failure')
    dump('recovery_archive_validation.json',dict(passed=True,archive=backup.relative_to(ROOT).as_posix(),sha256=sha(backup),archive_bytes=backup.stat().st_size,verified_members=len(selected),uncompressed_bytes=sum(r['expected_size'] for r in selected),scope='exact preflight eligible file contents; no entire package/data copy'))
    ledger(rows)  # durable decisions before first mutation
    deleted=[]
    try:
        for r in selected:
            p=(ROOT/r['path']).resolve()
            if not p.is_relative_to(VENDOR) or p.stat().st_size!=r['expected_size'] or sha(p)!=r['expected_sha256']:raise RuntimeError('Immediate final deletion guard failed:'+r['path'])
            # Individual files only. No recursive directory deletion or shell composition.
            p.unlink();deleted.append(r)
            r.update(action='deleted',reason='nonruntime_test_or_example;static_reference_checks_and_guarded_probe_passed;offline_backup_SHA256_verified',backup_archive=backup.relative_to(ROOT).as_posix(),backup_member=r['path'],deleted_bytes=r['expected_size'],timestamp_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
            ledger(rows)
        after=probe();dump('import_probe_after.json',after)
        before_sig=[(r['module'],r['ok'],r.get('path'),r.get('checks')) for r in before['results']]
        after_sig=[(r['module'],r['ok'],r.get('path'),r.get('checks')) for r in after['results']]
        if not after['all_ok'] or before_sig!=after_sig:raise RuntimeError('Post-deletion functionality changed')
    except Exception as e:
        # Stop all further deletion; restore only our already-deleted exact files.
        with zipfile.ZipFile(backup) as z:
            for r in deleted:
                p=(ROOT/r['path']).resolve()
                if not p.is_relative_to(VENDOR):raise RuntimeError('Restore path escaped vendor')
                if p.exists():raise RuntimeError('Restore would overwrite unexpected file')
                with p.open('xb') as f:f.write(z.read(r['path']))
                if sha(p)!=r['expected_sha256']:raise RuntimeError('Restored content hash mismatch')
                r.update(action='restored_after_error',deleted_bytes=0,reason=str(e))
        ledger(rows);dump('cleanup_execution_error.json',dict(error=str(e),further_deletions_stopped=True,restored_files=len(deleted)));raise
    ledger(rows)
    print(json.dumps(dict(deleted_files=len(deleted),deleted_bytes=sum(r['deleted_bytes'] for r in deleted),backup_bytes=backup.stat().st_size,actions=collections.Counter(r['action'] for r in rows),post_import_probe_all_ok=after['all_ok']),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
