"""Read-only project structure survey. Writes only new survey artifacts; no project imports."""
from pathlib import Path
import os,csv,json,re,hashlib,subprocess,collections,datetime

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
GENERATED={'PROJECT_CLEANUP_PLAN.md','PROJECT_FILE_INVENTORY.csv'}
def rel(p):return p.relative_to(ROOT).as_posix()
def dump(name,data):
    (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
def csvout(name,rows,fields):
    with (ROOT/name if '/' not in name else ROOT/name).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def digest(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()
def git(*args,input=None):
    env=dict(os.environ,GIT_OPTIONAL_LOCKS='0')
    q=subprocess.run(['git','-c','core.quotepath=false',*args],cwd=ROOT,input=input,capture_output=True,env=env)
    return q.returncode,q.stdout.decode('utf-8','replace'),q.stderr.decode('utf-8','replace')

def main():
    started=datetime.datetime.now(datetime.timezone.utc).isoformat()
    files=[];directories=[];errors=[];excluded=[]
    for base,ds,fs in os.walk(ROOT,followlinks=False,onerror=lambda e:errors.append(dict(path=str(e.filename),error='directory_enumeration:'+str(e)))):
        for d in ds:directories.append(rel(Path(base)/d))
        ds[:]=[d for d in ds if not (Path(base)/d).is_symlink()]
        for name in fs:
            p=Path(base)/name;r=rel(p)
            if r.startswith('docs/project_structure_review/') or r in GENERATED:
                excluded.append(r);continue
            try:
                st=p.stat();files.append(dict(path=r,bytes=st.st_size,mtime_ns=st.st_mtime_ns,
                    modified_at=datetime.datetime.fromtimestamp(st.st_mtime,datetime.timezone.utc).isoformat(),
                    extension=p.suffix.lower() or '(none)',symlink=p.is_symlink()))
            except OSError as e:errors.append(dict(path=r,error=str(e)))
    bypath={x['path']:x for x in files}; byname=collections.defaultdict(list)
    for x in files:byname[Path(x['path']).name].append(x['path'])
    namegroups={name:paths for name,paths in byname.items() if len(paths)>1}
    dump('filename_groups.json',namegroups)
    code,tracked,err=git('ls-files','-z');tracked=set(tracked.strip('\x00').split('\x00'))
    paths=[x['path'] for x in files if not x['path'].startswith('.git/')]
    _,ignored,_=git('check-ignore','--no-index','-z','--stdin',input=('\x00'.join(paths)+'\x00').encode('utf-8'))
    ignored=set(ignored.strip('\x00').split('\x00'))
    _,status,_=git('status','--porcelain=v1','--untracked-files=all')
    dump('git_snapshot_before.json',dict(status=status,tracked=sorted(tracked),ignored_count=len(ignored),error=err))
    # Full hashes only for non-Git, non-secret files; streaming 4 MiB blocks.
    # This also establishes an unchanged-content baseline for research artifacts.
    sizes=collections.defaultdict(list);hashes={}
    for x in files:
        p=x['path'];sizes[x['bytes']].append(p)
        if not p.startswith('.git/') and p!='.env' and not p.startswith('.env.') and not x['symlink']:
            try:hashes[p]=digest(ROOT/p)
            except OSError as e:errors.append(dict(path=p,error=str(e)))
    duplicates=collections.defaultdict(list)
    for p,h in hashes.items():duplicates[(bypath[p]['bytes'],h)].append(p)
    groups=[dict(bytes=k[0],sha256=k[1],paths=v,extra_copy_bytes=k[0]*(len(v)-1)) for k,v in duplicates.items() if len(v)>1]
    dump('duplicate_groups.json',groups)
    # Reference discovery: project source, notebook SOURCE cells only, Markdown,
    # JSON manifests, JSONL records, and path-bearing CSV columns. Never execute.
    refs=[];incoming=collections.defaultdict(set);scanlog=[];absrefs=[]
    names=sorted((n for n in byname if len(n)>5),key=len,reverse=True)
    namepattern=re.compile('|'.join(re.escape(n) for n in names))
    known_ext=re.compile(r'[^\s\x00<>"\'`|;,{}\[\]()=]+\.(?:csv|jsonl?|md|py|ipynb|html|txt|gz|sqlite|log|chm|pyd)\b',re.I)
    absol=re.compile(r'[A-Za-z]:[\\/][^\n\r"\'<>|]*')
    for x in files:
        p=ROOT/x['path'];r=x['path'];ext=p.suffix.lower()
        if r.startswith('.git/') or '/vendor/' in r or r=='.env' or r.startswith('.env.'):
            scanlog.append(dict(path=r,scope='excluded_git_secret_or_third_party'));continue
        chunks=[];scope='not_path_source'
        try:
            if ext=='.ipynb':
                obj=json.loads(p.read_text(encoding='utf-8-sig'));scope='notebook_source_cells_only'
                chunks=[(f'cell:{i+1}', ''.join(c.get('source',[]))) for i,c in enumerate(obj.get('cells',[]))]
            elif ext=='.csv':
                with p.open(encoding='utf-8-sig',newline='') as f:
                    rd=csv.DictReader(f);cols=[c for c in (rd.fieldnames or []) if re.search(r'path|file|manifest|directory',c,re.I) and not re.search(r'profile|image|url',c,re.I)]
                    if cols:
                        scope='CSV_path_columns:'+','.join(cols)
                        # Stream path-bearing columns; no article text or metadata analysis.
                        for i,row in enumerate(rd,2):
                            for c in cols:
                                v=row.get(c) or ''
                                if v:chunks.append((f'row:{i}:{c}',v))
                    else:scope='CSV_header_only_no_path_columns'
            elif ext in {'.py','.md','.json','.jsonl','.log','.txt'} and x['bytes']<=2_000_000 and '/texts/' not in r:
                data=p.read_bytes();enc='utf-16' if data.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig'
                text=data.decode(enc,errors='replace');chunks=list(enumerate(text.splitlines(),1));scope='full_small_text'
            else:scope='content_not_scanned_size_or_nonpath_artifact'
            for loc,text in chunks:
                if 'https://' in text and len(text)>3000 and ext not in {'.py','.md'}:continue
                matched=set(namepattern.findall(text))
                for n in matched:
                    targets=[t for t in byname[n] if t!=r and not t.startswith('.git/')]
                    for target in targets:
                        incoming[target].add(r)
                        refs.append(dict(source=r,location=str(loc),target=target,kind='filename_occurrence_not_resolved_execution',ambiguous_basename=len(targets)>1))
                for m in absol.finditer(text):
                    val=m.group(0).replace('\\\\','\\').strip()[:500]
                    absrefs.append(dict(source=r,location=str(loc),absolute_path=val,within_workspace=str(ROOT).casefold() in val.casefold(),action='report_only_do_not_rewrite'))
                # Missing path literals help distinguish deleted historical input from current files.
                if ext in {'.py','.ipynb','.md'}:
                    for token in known_ext.findall(text):
                        n=token.replace('\\','/').rsplit('/',1)[-1]
                        if n not in byname and not token.startswith(('http:','https:')):
                            refs.append(dict(source=r,location=str(loc),target=token[:250],kind='unresolved_or_generated_path_literal',ambiguous_basename=False))
            scanlog.append(dict(path=r,scope=scope,units=len(chunks)))
        except Exception as e:errors.append(dict(path=r,error='reference_scan:'+str(e)))
    refbase='docs/project_structure_review/'
    csvout(refbase+'path_references.csv',refs,['source','location','target','kind','ambiguous_basename'])
    csvout(refbase+'absolute_path_references.csv',absrefs,['source','location','absolute_path','within_workspace','action'])
    dump('reference_scan_scope.json',scanlog)
    # Existing preservation manifests impose dependencies even when no runnable
    # script mentions a file. Record exact snapshot-key relations separately.
    protected=collections.defaultdict(set)
    def walk(v,source):
        if isinstance(v,dict):
            for k,a in v.items():
                normal=k.replace('\\','/')
                if normal in bypath:protected[normal].add(source)
                walk(a,source)
        elif isinstance(v,list):
            for a in v:walk(a,source)
    for x in files:
        if x['extension']=='.json' and any(t in Path(x['path']).name for t in ['preservation','snapshot','hashes']):
            try:walk(json.loads((ROOT/x['path']).read_text(encoding='utf-8-sig')),x['path'])
            except Exception as e:errors.append(dict(path=x['path'],error='manifest:'+str(e)))
    inventory=[];deletions=[]
    groupfor={p:f'sha256_group_{i+1}' for i,g in enumerate(groups) for p in g['paths']}
    for x in files:
        r=x['path'];p=Path(r);ext=x['extension'];isgit=r.startswith('.git/')
        role='needs_review';state='needs_review';risk='high';target=r
        cat='must_keep';reason='역할 또는 대체 가능성이 확정되지 않아 보존';recovery='확인되지 않음'
        if isgit:role='Git_history';state='reference';reason='Git 이력·객체·설정; 변경 금지'
        elif r in {'.env','.env.example','.gitignore','LICENSE'} or r.startswith('.vscode/'):
            role='configuration';state='active';reason='환경·보안·라이선스·Git/IDE 설정 보존'
        elif r in {'hormuz_2026_data/01_newscatcher_raw.csv','hormuz_2026_data/05_extraction_candidates_v2.csv'}:
            role='A_original_core_metadata';state='active';reason='사용자 지정 절대 보존 원본/권위 후보 메타데이터'
        elif ext=='.ipynb':role='B_notebook';state='reference' if p.name=='hormuz_textminig.ipynb' else 'active';reason='수집·정규화·벤치마크 코드와 출력 이력; 버전 간 역할 다름'
        elif r.startswith('docs/') or ext=='.md':role='D_document';state='active' if r.startswith('docs/') or r=='README.md' else 'reference';risk='medium';reason='연구 근거·계획·실행 설명 보존'
        elif '/vendor/' in r:
            role='E_dependency_residue';state='needs_review';reason='실험 코드가 vendor를 sys.path에 추가; 잔여 패키지·바이너리 일괄 삭제 불가'
            if p.name=='PyWin32.chm':
                cat='delete_after_review';reason='Windows 패키지 도움말; 프로젝트 코드·본문 추출에서 직접 참조 없음. vendor 동적 접근·패키지 복구 조건 확인 필요';recovery='과거 설치 로그의 pywin32 버전으로 재설치 가능하나 동일 배포물 오프라인 복구는 미확인';risk='low'
            if any(part in {'test','tests','examples','demos','demo','docs','doc'} for part in p.parts):
                cat='delete_after_review';reason='패키지의 테스트·예제·도움말 경로. 프로젝트 코드의 직접 참조는 발견되지 않았으나 패키지 내부 import·리소스 접근까지 미검증; 환경 재현·라이선스 및 RECORD 영향 검토 후 승인 필요';recovery='설치 로그/배포 버전으로 패키지 재설치 가능성; 동일 파일 오프라인 복구 보장 없음';risk='medium'
        elif r.startswith('hormuz_2026_data/'):
            role='C_experiment_evidence';state='reference';reason='단계별 분석·수집·감사 근거; 미래 재현·재추출에 필요'
            if ext=='.py':role='B_stage_reproduction_code';reason='단계 재현 코드; 보관 위치 자체가 실행 기준 경로'
            if ext in {'.html','.txt'} or '/texts/' in r:reason='기존 HTML·전체 추출문은 재추출/오류 검증 근거; 캐시 명칭과 무관하게 보존'
        if ext in {'.pyc','.pyo'} or '__pycache__' in p.parts or '.pytest_cache' in p.parts:
            role='E_regenerable_cache';state='temporary';cat='safe_to_delete';risk='low';reason='Python 바이트코드/테스트 캐시; 원본 소스와 환경에서 재생성 가능';recovery='Python/pytest가 재생성'
        # Only proven empty diagnostic streams: no content, retain references and
        # exclude protected snapshots from the automatic class.
        if ext=='.log' and x['bytes']==0:
            role='E_empty_diagnostic_log';state='temporary';cat='safe_to_delete' if not protected[r] else 'delete_after_review';risk='low' if not protected[r] else 'medium';reason='0바이트 진단 로그; 기록 내용 없음'+(' (기존 해시 manifest가 존재하므로 검증 계약 승인 필요)' if protected[r] else '');recovery='같은 경로에 빈 파일 재생성 가능; 실행 시각 메타데이터는 별도 보존'
        # Do not identify historical evidence as disposable merely because a
        # newer analysis exists. This important large item remains explicit.
        if p.name=='04_row_selection_audit.csv':
            cat='delete_after_review';reason='약 50MB 구버전 행별 파생 감사. 최신 URL 원장과 의미가 동일하다는 증거 없음; 과거 컬럼/기준·manifest 참조 확인 후 승인 가능';recovery='analyze_candidates.py와 당시 입력으로 재생성 가능성; 현재 삭제된 05_article_index 의존 구간은 확인 필요'
        if p.name=='selection_decision_ledger.csv':
            reason='최신 기사별 선정 근거와 원본 대응 원장; A/B/C 과거 기록 보존. 큰 파일이나 이번 삭제 후보 아님'
        if r not in hashes and not isgit and not r.startswith('.env'):
            if '/vendor/' in r:state='needs_review'
        dup=groupfor.get(r,'');same=byname[p.name]
        needs=sorted(incoming[r]|protected[r])
        inventory.append(dict(current_relative_path=r,size_bytes=x['bytes'],extension=ext,modified_at_utc=x['modified_at'],git_tracked='1' if r in tracked else '0',git_ignored='1' if r in ignored else '0',role=role,activity_status=state,proposed_path=target,move_risk=risk,references_requiring_update=';'.join(needs),deletion_class=cat,deletion_reason=reason,recovery=recovery,duplicate_status='content_identical' if dup else 'same_name_only' if len(same)>1 else 'not_detected' if r in hashes else 'not_checked',duplicate_evidence=dup or ('filename_groups.json key='+p.name if len(same)>1 else 'streaming SHA256' if r in hashes else 'hash_unavailable_or_excluded'),sha256=hashes.get(r,''),preservation_manifests=';'.join(sorted(protected[r]))))
        if cat!='must_keep':deletions.append(inventory[-1])
    csvout('PROJECT_FILE_INVENTORY.csv',inventory,list(inventory[0]))
    csvout(refbase+'deletion_candidates.csv',deletions,list(inventory[0]))
    def agg(key):
        a=collections.defaultdict(lambda:dict(files=0,bytes=0))
        for x in files:a[key(x)]['files']+=1;a[key(x)]['bytes']+=x['bytes']
        return dict(sorted(a.items(),key=lambda t:t[1]['bytes'],reverse=True))
    folders=agg(lambda x:x['path'].split('/')[0] if '/' in x['path'] else '(root)')
    subfolders=agg(lambda x:'/'.join(x['path'].split('/')[:2]) if x['path'].startswith('hormuz_2026_data/') and x['path'].count('/')>1 else '(other)')
    foldersall=collections.defaultdict(lambda:dict(files=0,bytes=0))
    for x in files:
        for parent in Path(x['path']).parents:
            k=parent.as_posix();foldersall[k]['files']+=1;foldersall[k]['bytes']+=x['bytes']
    csvout(refbase+'folder_sizes.csv',[dict(path=k,**v) for k,v in sorted(foldersall.items())],['path','files','bytes'])
    snapshot=dict(started_utc=started,files=files,directories=directories,errors=errors,excluded_survey_outputs=excluded)
    dump('baseline.json',snapshot)
    dump('content_hashes.json',hashes)
    summary=dict(baseline_files=len(files),baseline_directories=len([p for p in directories if not p.startswith('docs/project_structure_review')]),baseline_bytes=sum(x['bytes'] for x in files),top_folders=folders,data_subfolders=subfolders,types=agg(lambda x:x['extension']),largest=sorted(files,key=lambda x:x['bytes'],reverse=True)[:30],duplicate_groups=groups,duplicate_extra_bytes=sum(g['extra_copy_bytes'] for g in groups),hashed_files=len(hashes),hashed_bytes=sum(bypath[p]['bytes'] for p in hashes),reference_occurrences=len(refs),absolute_path_occurrences=len(absrefs),preserved_manifest_targets=sum(bool(v) for v in protected.values()),git_tracked=len(tracked),git_ignored=len(ignored),deletion_totals={c:dict(files=sum(x['deletion_class']==c for x in inventory),bytes=sum(x['size_bytes'] for x in inventory if x['deletion_class']==c)) for c in ['safe_to_delete','delete_after_review','must_keep']},errors=errors)
    dump('survey_summary.json',summary)
    print(json.dumps({k:v for k,v in summary.items() if k in {'baseline_files','baseline_directories','baseline_bytes','deletion_totals','hashed_files','reference_occurrences','errors'}},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
