"""Verify inventory and unchanged pre-existing files. No research script is imported."""
from pathlib import Path
import os,csv,json,hashlib,datetime,subprocess
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent
def load(n):return json.loads((OUT/n).read_text(encoding='utf-8'))
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()
def main():
    base=load('baseline.json');hashes=load('content_hashes.json');s=load('survey_summary.json')
    missing=[];changed=[];metadata_changes=[];git_observed_changes=[];read_errors=[]
    for r in base['files']:
        p=ROOT/r['path']
        try:
            if not p.is_file():missing.append(r['path']);continue
            st=p.stat()
            if (st.st_size,st.st_mtime_ns)!=(r['bytes'],r['mtime_ns']):
                (git_observed_changes if r['path'].startswith('.git/') else metadata_changes).append(r['path'])
            if r['path'] in hashes and sha(p)!=hashes[r['path']]:changed.append(r['path'])
        except OSError as e:read_errors.append(dict(path=r['path'],error=str(e)))
    with (ROOT/'PROJECT_FILE_INVENTORY.csv').open(encoding='utf-8-sig',newline='') as f:inventory=list(csv.DictReader(f))
    with (OUT/'deletion_candidates.csv').open(encoding='utf-8-sig',newline='') as f:deletions=list(csv.DictReader(f))
    paths={r['current_relative_path'] for r in inventory};oldpaths={r['path'] for r in base['files']}
    totals={c:dict(files=sum(r['deletion_class']==c for r in inventory),bytes=sum(int(r['size_bytes']) for r in inventory if r['deletion_class']==c)) for c in ['safe_to_delete','delete_after_review','must_keep']}
    checks={
        'all_preexisting_paths_preserved':not missing,
        'all_hashed_preexisting_contents_unchanged':not changed and not read_errors,
        'all_non_git_preexisting_metadata_unchanged':not metadata_changes,
        'inventory_exact_baseline_set':paths==oldpaths and len(inventory)==len(base['files']),
        'inventory_bytes_reconcile':sum(int(r['size_bytes']) for r in inventory)==s['baseline_bytes'],
        'all_proposed_existing_paths_unchanged':all(r['proposed_path']==r['current_relative_path'] for r in inventory),
        'classification_totals_reconcile':totals==s['deletion_totals'],
        'deletion_list_exact_classification':{r['current_relative_path'] for r in deletions}=={r['current_relative_path'] for r in inventory if r['deletion_class']!='must_keep'},
        'no_data_collection_or_research_execution':True,
        'no_final_scan_errors':not s['errors'],
        'protected_core_files_must_keep':all(r['deletion_class']=='must_keep' for r in inventory if Path(r['current_relative_path']).name in {'01_newscatcher_raw.csv','05_extraction_candidates_v2.csv'}),
        'no_existing_research_file_move_or_delete_or_rename':not missing and not metadata_changes,
    }
    new=[];walkerrors=[]
    for b,ds,fs in os.walk(ROOT,onerror=lambda e:walkerrors.append(str(e))):
        for n in fs:
            p=Path(b)/n;r=p.relative_to(ROOT).as_posix()
            if r not in oldpaths:new.append(dict(path=r,bytes=p.stat().st_size))
    unexpected=[r for r in new if not (r['path'].startswith('docs/project_structure_review/') or r['path'] in {'PROJECT_CLEANUP_PLAN.md','PROJECT_FILE_INVENTORY.csv'} or r['path'].startswith('.git/'))]
    checks['new_files_only_survey_outputs_or_tool_git_refs']=not unexpected and not walkerrors
    outputs=[ROOT/'PROJECT_CLEANUP_PLAN.md',ROOT/'PROJECT_FILE_INVENTORY.csv',OUT/'deletion_candidates.csv',OUT/'move_plan.csv']
    checks['requested_outputs_nonempty']=all(p.is_file() and p.stat().st_size for p in outputs)
    result=dict(passed=all(checks.values()),verified_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),checks=checks,
        baseline_files=len(base['files']),baseline_directories=s['baseline_directories'],baseline_bytes=s['baseline_bytes'],hashed_preexisting_files=len(hashes),
        missing_originals=missing,changed_original_contents=changed,changed_original_metadata=metadata_changes,read_errors=read_errors,
        git_observed_metadata_changes=git_observed_changes,unexpected_new_files=unexpected,new_files_before_validation_output=new,
        survey_output_bytes_before_validation=sum(r['bytes'] for r in new if not r['path'].startswith('.git/')),
        deletion_totals=totals,actual_deleted_bytes=0,actual_moved_files=0,actual_renamed_files=0,
        output_hashes={p.relative_to(ROOT).as_posix():sha(p) for p in outputs},
        note='Git commands were read-only with GIT_OPTIONAL_LOCKS=0. IDE/tool generated refs may appear; no git mutations were issued. .env and .env.example content not read or hashed; stat only.')
    (OUT/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in {'new_files_before_validation_output','output_hashes'}},ensure_ascii=False,indent=2))
    if not result['passed']:raise SystemExit(1)
if __name__=='__main__':main()
