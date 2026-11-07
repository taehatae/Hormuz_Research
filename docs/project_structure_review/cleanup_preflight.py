"""Read-only second-stage eligibility checks. No research code executed."""
from pathlib import Path
import ast,csv,json,hashlib,collections,re,sys,subprocess,os,zipfile
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent
VENDOR=ROOT/'hormuz_2026_data/body_collection_pilot_20261102_01/vendor'
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()
def readcsv(p):
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def dump(n,v):(OUT/n).write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
def main():
    rows=readcsv(OUT/'deletion_candidates.csv');inventory={r['current_relative_path']:r for r in readcsv(ROOT/'PROJECT_FILE_INVENTORY.csv')}
    old= json.loads((OUT/'validation.json').read_text(encoding='utf-8'))
    for n,h in old['output_hashes'].items():
        if sha(ROOT/n)!=h:raise RuntimeError('Stage1 output hash mismatch: '+n)
    candidates={r['current_relative_path']:r for r in rows if '/vendor/' in r['current_relative_path']}
    assert len(candidates)==722 and len(rows)==726
    # Baseline is taken before README edits/folder creation/deletion, excludes
    # this second-stage tooling and future execution artifacts from the count.
    current=[];errors=[]
    for base,dirs,names in os.walk(ROOT,onerror=lambda e:errors.append(str(e))):
        for n in names:
            p=Path(base)/n;st=p.stat();current.append(dict(path=p.relative_to(ROOT).as_posix(),bytes=st.st_size,mtime_ns=st.st_mtime_ns))
    if errors:raise RuntimeError(str(errors))
    dump('execution_before.json',dict(files=current,file_count=len(current),bytes=sum(r['bytes'] for r in current),readme_sha256=sha(ROOT/'README.md')))
    # All project and third-party Python sources are parsed, never imported here.
    pyfiles=list(VENDOR.rglob('*.py'))
    modules={}
    for p in pyfiles:
        parts=list(p.relative_to(VENDOR).with_suffix('').parts)
        if parts[-1]=='__init__':parts.pop()
        modules['.'.join(parts)]=p
    candmods={m:p.relative_to(ROOT).as_posix() for m,p in modules.items() if p.relative_to(ROOT).as_posix() in candidates}
    trees={};parse_errors=[];imports=collections.defaultdict(set);dynamic=[]
    for m,p in modules.items():
        try:trees[m]=ast.parse(p.read_text(encoding='utf-8-sig'),filename=str(p))
        except (SyntaxError,UnicodeError) as e:parse_errors.append(dict(path=p.relative_to(ROOT).as_posix(),error=str(e)))
    for m,t in trees.items():
        package=m if modules[m].name=='__init__.py' else m.rpartition('.')[0]
        for node in ast.walk(t):
            if isinstance(node,ast.Import):imports[m].update(a.name for a in node.names)
            elif isinstance(node,ast.ImportFrom):
                prefix=node.module or ''
                if node.level:
                    parent=package.split('.')
                    base='.'.join(parent[:len(parent)-node.level+1])
                    prefix='.'.join(x for x in [base,prefix] if x)
                imports[m].add(prefix)
                imports[m].update(prefix+'.'+a.name for a in node.names if a.name!='*')
            elif isinstance(node,ast.Call) and isinstance(node.func,(ast.Name,ast.Attribute)):
                fn=node.func.id if isinstance(node.func,ast.Name) else node.func.attr
                if fn in {'import_module','__import__','get_data','files','resource_filename','resource_string','open','joinpath'}:
                    dynamic.append(dict(source=m,call=fn,literals=[a.value for a in node.args if isinstance(a,ast.Constant) and isinstance(a.value,str)]))
    external_refs=collections.defaultdict(set)
    for m,imps in imports.items():
        if m in candmods:continue
        for target in imps:
            for cm,path in candmods.items():
                if target==cm or target.startswith(cm+'.'):external_refs[path].add(modules[m].relative_to(ROOT).as_posix())
    # Conservative package-family hold: code in docs/ can be boto documentation
    # runtime, test helper modules may be imported by production/trial modules.
    held_families={Path(p).relative_to(VENDOR.relative_to(ROOT)).parts[0] for p in external_refs}
    project_refs=collections.defaultdict(set)
    for p in ROOT.rglob('*.py'):
        if VENDOR in p.parents or OUT in p.parents:continue
        text=p.read_text(encoding='utf-8-sig')
        for r in candidates:
            vp=r.split('/vendor/',1)[1];mod=vp[:-3].replace('/','.') if vp.endswith('.py') else vp
            if vp in text or ('.' in mod and mod in text):project_refs[r].add(p.relative_to(ROOT).as_posix())
    # Detect textual help/resource references outside candidate paths too.
    resource_refs=collections.defaultdict(set)
    doc_candidates={r for r in candidates if not r.endswith('.py')}
    for m,p in modules.items():
        pr=p.relative_to(ROOT).as_posix()
        if pr in candidates:continue
        try:text=p.read_text(encoding='utf-8-sig').casefold()
        except UnicodeError:continue
        for r in doc_candidates:
            name=Path(r).name.casefold();vp=r.split('/vendor/',1)[1].casefold()
            if (len(name)>8 and name in text) or vp in text:resource_refs[r].add(pr)
    probe=subprocess.run([sys.executable,'-B',str(OUT/'cleanup_probe.py')],cwd=ROOT,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8'),capture_output=True,text=True,encoding='utf-8',timeout=60)
    if probe.returncode:raise RuntimeError(probe.stderr)
    before=json.loads(probe.stdout);dump('import_probe_before.json',before)
    # Baseline failure restricts affected families: never claim an originally
    # broken package was validated or allow deletion of its candidate tree.
    failed={r['module'].split('.')[0] for r in before['results'] if not r['ok']}
    decisions=[]
    for r in rows:
        path=r['current_relative_path'];p=ROOT/path
        current_sha=sha(p);match=p.stat().st_size==int(r['size_bytes']) and current_sha==r['sha256'] and r['sha256']==inventory[path]['sha256']
        status='preserve_user';reasons=['explicit_user_preservation']
        if path in candidates:
            vp=p.relative_to(VENDOR);family=vp.parts[0];status='eligible';reasons=[]
            if not match:status='hold';reasons.append('size_or_SHA256_mismatch')
            if any(x in p.name.casefold() for x in ['license','copying','copyright','notice','metadata','record','wheel','installer']):status='hold';reasons.append('license_or_required_metadata_or_uncertain_name')
            if project_refs[path]:status='hold';reasons.append('project_direct_reference')
            if family in held_families:status='hold';reasons.append('package_family_has_runtime_import_into_candidate_tree')
            if family in failed:status='hold';reasons.append('baseline_package_import_not_verified')
            if resource_refs[path]:status='hold';reasons.append('runtime_resource_or_help_reference')
            if p.suffix.lower() in {'.pem','.no_trailing_newline'} or p.name=='not-a-certificate':status='hold';reasons.append('test_security_fixture_dynamic_access_not_verified')
            if any(e['path'].split('/vendor/',1)[-1].split('/')[0]==family for e in parse_errors):status='hold';reasons.append('family_source_parse_incomplete')
            if family in {'win32','win32com','win32comext','isapi','adodbapi','PyWin32.chm'}:
                status='hold';reasons.append('Windows_COM_dynamic_registration_or_help_execution_not_tested')
        decisions.append(dict(path=path,expected_size=int(r['size_bytes']),expected_sha256=r['sha256'],actual_sha256=current_sha,precheck_match=match,decision=status,reasons=reasons,direct_project_refs=sorted(project_refs[path]),runtime_import_refs=sorted(external_refs[path]),resource_refs=sorted(resource_refs[path]),recovery='verified_local_compressed_backup_required_before_delete' if status=='eligible' else 'not_deleted'))
    dump('cleanup_preflight.json',dict(decisions=decisions,held_runtime_families=sorted(held_families),parse_errors=parse_errors,import_probe_before=before,network_requests=0,original_analysis_runs=0,dynamic_call_count=len(dynamic)))
    dump('runtime_reference_details.json',dict(imports={k:sorted(v) for k,v in imports.items() if k not in candmods},dynamic_resource_calls=dynamic))
    print(json.dumps(dict(decisions=collections.Counter(r['decision'] for r in decisions),eligible_bytes=sum(r['expected_size'] for r in decisions if r['decision']=='eligible'),held_runtime_families=sorted(held_families),parse_errors=len(parse_errors),import_probe=before),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
