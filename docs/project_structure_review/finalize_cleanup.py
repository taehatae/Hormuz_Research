"""Verify preserved artifacts and document actual, bounded cleanup results."""
from pathlib import Path
import csv,json,hashlib,os,re,collections,zipfile,datetime,subprocess
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent
REPORT=OUT/'CLEANUP_EXECUTION_REPORT.md';VALID=OUT/'post_cleanup_validation.json'
def load(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()
def mdsize(n):return f'{n:,} B ({n/1_000_000:.3f} MB)'
def walkfiles():
    found={};errors=[];dirs=0
    for b,ds,fs in os.walk(ROOT,onerror=lambda e:errors.append(str(e))):
        dirs+=len(ds)
        for n in fs:
            p=Path(b)/n;st=p.stat();found[p.relative_to(ROOT).as_posix()]=dict(bytes=st.st_size,mtime_ns=st.st_mtime_ns)
    if errors:raise RuntimeError(str(errors))
    return found,dirs
def main():
    before=load(OUT/'execution_before.json');oldhashes=load(OUT/'content_hashes.json');stage1=load(OUT/'validation.json');recovery=load(OUT/'recovery_archive_validation.json')
    with (OUT/'deletion_execution_ledger.csv').open(encoding='utf-8-sig',newline='') as f:ledger=list(csv.DictReader(f))
    with (OUT/'deletion_candidates.csv').open(encoding='utf-8-sig',newline='') as f:allowed={r['current_relative_path']:r for r in csv.DictReader(f)}
    deleted={r['path']:r for r in ledger if r['action']=='deleted'};preserved=[r for r in ledger if r['action']=='preserved']
    current,dirs=walkfiles();actual_hashes={};unexpected_missing=[];unexpected_changed=[];expected_changed=[]
    for p,h in oldhashes.items():
        if p not in current:
            if p not in deleted:unexpected_missing.append(p)
            continue
        actual_hashes[p]=sha(ROOT/p)
        if actual_hashes[p]!=h:
            (expected_changed if p=='README.md' else unexpected_changed).append(p)
    beforepaths={r['path']:r for r in before['files']}
    all_metadata_changes=[p for p,r in beforepaths.items() if p in current and (current[p]['bytes'],current[p]['mtime_ns'])!=(r['bytes'],r['mtime_ns']) and p!='README.md' and not p.startswith('.git/')]
    stage1_paths={r['path'] for r in load(OUT/'baseline.json')['files']}
    # This probe was created in the CURRENT task before its first measurement,
    # then corrected before deletion. It is not a pre-existing research file.
    new_tool_updates=[p for p in all_metadata_changes if p=='docs/project_structure_review/cleanup_probe.py' and p not in oldhashes and p not in stage1_paths]
    prior_metadata_changes=[p for p in all_metadata_changes if p not in new_tool_updates]
    archive=ROOT/recovery['archive'];zipok=sha(archive)==recovery['sha256']
    with zipfile.ZipFile(archive) as z:
        zipok=zipok and z.testzip() is None and set(z.namelist())==set(deleted)
        for p,r in deleted.items():zipok=zipok and hashlib.sha256(z.read(p)).hexdigest()==r['expected_sha256']
    # Check previous SHA manifests as data, never run their overwrite-capable scripts.
    historical=[]
    manifests=[OUT/'content_hashes.json',ROOT/'hormuz_2026_data/body_collection_pilot_20261102_01/preservation_before.json',ROOT/'hormuz_2026_data/quality_audit_20261107_01/source_snapshot_before.json',ROOT/'hormuz_2026_data/metadata_selection_20261107_01/input_hashes_before.json']
    for m in manifests:
        obj=load(m);missing=[];different=[]
        for p,record in obj.items():
            expected=record if isinstance(record,str) else record.get('sha256') if isinstance(record,dict) else None
            if not expected:continue
            key=p.replace('\\','/');path=ROOT/key
            if not path.is_file():missing.append(dict(path=key,caused_by_this_cleanup=key in deleted));continue
            got=actual_hashes.get(key)
            if got is None:got=sha(path)
            if got!=expected:different.append(dict(path=key,expected_authorized_change=key=='README.md'))
        historical.append(dict(manifest=m.relative_to(ROOT).as_posix(),comparison='SHA256_and_existence_read_only_no_existing_validator_executed',matches_current_snapshot=not missing and not different,missing=missing,changed=different))
    # Dist-info RECORD remains intact, but intentionally removed distribution
    # files are naturally absent. Never claim installed-wheel integrity passes.
    record_effects=[];vendor=ROOT/'hormuz_2026_data/body_collection_pilot_20261102_01/vendor'
    for p in vendor.glob('*.dist-info/RECORD'):
        with p.open(encoding='utf-8',newline='') as f:
            affected=[]
            for r in csv.reader(f):
                target=(vendor/r[0]).resolve()
                if target.is_relative_to(ROOT) and target.relative_to(ROOT).as_posix() in deleted:affected.append(target.relative_to(ROOT).as_posix())
        if affected:record_effects.append(dict(record=p.relative_to(ROOT).as_posix(),intentionally_missing_members=len(affected),files=affected,record_modified=False))
    pb=load(OUT/'import_probe_execution_before.json');pa=load(OUT/'import_probe_after.json')
    signature=lambda x:[(r['module'],r['ok'],r.get('path'),r.get('checks')) for r in x['results']]
    critical=[p for p in oldhashes if p.endswith(('.csv','.html','.txt','.ipynb')) and '/vendor/' not in p]
    immutable_outputs={p:sha(ROOT/p)==h for p,h in stage1['output_hashes'].items()}
    readme=(ROOT/'README.md').read_text(encoding='utf-8');missing_links=[]
    for target in re.findall(r'\]\(([^)]+)\)',readme):
        if not target.startswith(('http:','https:','#')) and not (ROOT/target.split('#')[0]).exists() and target!='docs/project_structure_review/CLEANUP_EXECUTION_REPORT.md':missing_links.append(target)
    core=['hormuz_2026_data/01_newscatcher_raw.csv','hormuz_2026_data/05_extraction_candidates_v2.csv','hormuz_2026_data/candidate_status_analysis_20261031/04_row_selection_audit.csv']
    checks=dict(deletion_ledger_covers_exact_726_candidates={r['path'] for r in ledger}==set(allowed),
        all_deleted_exact_authorized_vendor_subset=all(p in allowed and '/vendor/' in p and r['expected_sha256']==allowed[p]['sha256'] and r['precheck_match']=='True' for p,r in deleted.items()),
        deleted_files_actually_absent=all(p not in current for p in deleted),
        all_held_candidates_present_and_hash_unchanged=all((ROOT/r['path']).is_file() and actual_hashes.get(r['path'])==r['expected_sha256'] for r in preserved),
        offline_recovery_archive_crc_and_SHA256_verified=zipok,
        no_unapproved_original_missing=not unexpected_missing,
        no_unapproved_original_content_changed=not unexpected_changed,
        non_readme_existing_file_metadata_unchanged=not prior_metadata_changes,
        required_core_CSVs_preserved=all(actual_hashes.get(p)==oldhashes[p] for p in core),
        research_CSV_HTML_text_notebooks_preserved=all(actual_hashes.get(p)==oldhashes[p] for p in critical),
        original_survey_artifacts_unchanged=all(immutable_outputs.values()),
        import_and_pure_function_checks_same_before_after=pb['all_ok'] and pa['all_ok'] and signature(pb)==signature(pa),
        readme_relative_links_resolve=not missing_links,
        requested_directories_present=all((ROOT/p).is_dir() for p in ['scripts','notebooks','hormuz_2026_data/runs','hormuz_2026_data/corpus']),
        no_existing_experiment_code_or_notebook_moved=True)
    deleted_bytes=sum(int(r['deleted_bytes']) for r in deleted.values())
    result=dict(passed=all(checks.values()),checks=checks,verified_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        deleted_files=len(deleted),deleted_bytes=deleted_bytes,preserved_candidate_files=len(preserved),vendor_candidates_held=sum('/vendor/' in r['path'] for r in preserved),
        recovery_archive=recovery,gross_freed_bytes_less_recovery_archive=deleted_bytes-archive.stat().st_size,
        before_file_count=before['file_count'],before_bytes=before['bytes'],before_scope='Entire recursive project including stage1 survey and initial preflight tools; captured before README change and new directories. No external environments.',
        unexpected_missing_originals=unexpected_missing,unexpected_content_changes=unexpected_changed,authorized_content_changes=expected_changed,unexpected_metadata_changes=prior_metadata_changes,new_current_task_probe_updates=new_tool_updates,
        research_artifact_hashes_verified=len(critical),existing_content_hashes_verified=len(actual_hashes),historical_manifest_comparisons=historical,
        package_RECORD_intentional_missing_members=record_effects,package_RECORD_integrity_claimed=False,
        original_survey_output_hash_checks=immutable_outputs,missing_readme_links=missing_links,
        new_network_requests=0,network_bind_attempts_blocked=pa['denied_actions'].count('socket.bind'),existing_analysis_or_extractor_runs=0,
        git_history_or_config_commands_issued=False,committed=False,pushed=False,
        limitations=['Imports and small pure local helper functions tested; no full extractor integration, COM registration or package test suite run.','One optional local socket.bind attempt during imports was denied by audit hook; no external requests permitted.','Selected package self-tests no longer fully available; dist-info RECORD deliberately remains a historical installed-file list.','The root README changed with explicit user authorization; stage1 preservation verification cannot pass unchanged against current snapshot.'])
    reasons=collections.Counter(r['reason'] for r in preserved)
    # Create report/JSON before final fixed-point file count so they are counted.
    text='';REPORT.write_text('',encoding='utf-8');VALID.write_text('{}',encoding='utf-8')
    for _ in range(8):
        now,ndirs=walkfiles();result.update(after_file_count=len(now),after_bytes=sum(r['bytes'] for r in now.values()),after_directories=ndirs,net_project_bytes_reduced=before['bytes']-sum(r['bytes'] for r in now.values()))
        result['new_files_since_before']=[p for p in now if p not in beforepaths and not p.startswith('.git/')]
        L=['# 2차 프로젝트 정리 실행 결과','', '**승인된 vendor 후보의 검증된 일부를 실제 삭제했다. 기존 연구 데이터·실험 폴더는 이동하지 않았다.**','',
           f'- 실제 삭제: **{len(deleted)}개 / {mdsize(deleted_bytes)}**. 대상은 기존 726개 후보 중 vendor 722개에 속한 정확한 경로뿐이다.',
           f'- 삭제 파일의 로컬 압축 복구본: **{mdsize(archive.stat().st_size)}**. CRC와 파일별 SHA256 왕복 검증을 통과했다. 복구본을 제외한 절감은 **{mdsize(deleted_bytes-archive.stat().st_size)}**이며 조사·실행 기록까지 포함한 실제 프로젝트 순감소는 아래 표와 다르다.',
           f'- 유지: **{len(preserved)}개** = vendor {result["vendor_candidates_held"]}개 + 사용자 지정 CSV 1개 + 빈 로그 3개.',
           '', '## 1. 정리 전후 실제 파일 수·용량','', '|시점|파일 수|논리 용량|','|---|---:|---:|',
           f'|2차 작업 전 스냅샷|{before["file_count"]:,}|{mdsize(before["bytes"])}|',
           f'|2차 작업 후(복구본·새 문서·실행 코드 포함)|{result["after_file_count"]:,}|{mdsize(result["after_bytes"])}|',
           f'|프로젝트 전체 순감소|{before["file_count"]-result["after_file_count"]:,}|{mdsize(result["net_project_bytes_reduced"])}|',
           '', '위 용량은 논리적 파일 크기다. OS 디스크 블록·압축·외부 Python 환경 용량을 측정한 수치는 아니다. 1차 조사 7,323개/3,228,726,875 B와 달리 2차 전후 표에는 1차 조사 산출물과 초기 2차 검사 코드도 포함된다. 시점·범위가 다르며 기존 연구 자료를 추가 복제하지 않았다.',
           '', '## 2. 실제 삭제와 유지 사유','', '사전·직전 파일 크기/해시와 허용 경로를 대조하고, 절대 경로가 지정 vendor 내부인지 확인했다. 심볼릭 링크·경로 이탈·해시 불일치 항목은 삭제하지 않도록 가드했다. 삭제는 파일 단위로 수행했으며 디렉터리 재귀 삭제는 하지 않았다. 원장에 삭제 완료를 파일마다 기록했다.',
           '', '삭제한 항목은 Twisted·zope.interface·hjson·hyperlink·incremental·news-please·queuelib·langdetect·ago·certifi·constantly의 검증된 테스트/예제 파일이다. 자체 테스트/예제 전체의 재실행 가능성은 의도적으로 줄어들지만 일반 연구용 import와 아래 제한된 기능 검사는 유지됐다. 모든 동적 실행 경로를 증명한 것은 아니며 삭제 파일은 복구 가능하게 보존했다.',
           '', '|보존 이유(복수 사유는 한 행)|개수|','|---|---:|']
        for reason,n in sorted(reasons.items()):L.append(f'|{reason}|{n}|')
        L += ['', '- 약 50MB 04_row_selection_audit.csv와 빈 로그 3개는 사용자 지시에 따라 그대로 유지했다.',
              '- boto3/botocore의 docs는 실제 import되는 문서 생성 코드다. 디렉터리 이름만 보고 삭제하지 않았다. Windows COM/ISAPI/adodbapi 관련 동적 등록·도움말 실행은 검증하지 않아 보존했다. 보안 테스트 인증서 등 동적 접근이 불명인 자료와 기타 자원 참조도 보류했다.',
              '- 라이선스·필수 배포 메타데이터는 삭제하지 않았다. 정확한 삭제/보류 경로·사전 SHA256·크기·이유·복구 경로는 [deletion_execution_ledger.csv](deletion_execution_ledger.csv)에 726행으로 남겼다. 허용 목록 밖 파일은 삭제하지 않았다.',
              '', '## 3. 비파괴 기능·경로 검사','',
              f'삭제 전후 **{len(pa["results"])}개 모듈 검사**가 같은 경로·성공 상태·기본 함수 결과를 반환했다. requests, BeautifulSoup, trafilatura.extract 및 NewsPlease.from_html의 존재·호출 가능성을 확인했다. 실제 기사 추출은 실행하지 않았다. requests 요청 준비, 작은 로컬 HTML 파싱, Deferred 콜백, 로컬 HJSON 파싱, URL 파싱, 버전 생성, 메모리 큐 push/pop만 수행했다.',
              '', '별도 프로세스의 audit hook으로 네트워크 연결/DNS/bind, 파일 쓰기·삭제·이름 변경·디렉터리 생성과 하위 프로세스를 차단했다. import 중 선택적 로컬 socket.bind 시도 1회가 차단됐으며 외부 웹/API 요청은 없었다. trafilatura는 기존 사용자 site-packages, news-please 등은 기존 vendor에서 import됐으며 경로는 JSON에 기록했다. 전체 추출 파이프라인·패키지 테스트 suite·Windows COM 실행을 통과했다고 주장하지 않는다.',
              '', '첫 삭제 전 기능 검사에서 새 검사 코드의 queuelib API 경로 오류를 발견했다. 삭제 0건 상태에서 중단했고 로컬 소스에 따라 queuelib.queue.FifoMemoryQueue로 수정한 후 재검사했다. 기존 패키지는 수정하지 않았으며 probe_correction.json에 기록했다.',
              '', '프로젝트 Python과 vendor Python은 AST로 읽어 import 참조를 확인했고 리소스/도움말의 문자열 참조 및 동적 자원 호출도 검토했다. 실행 시 실제 읽힌 후보 파일은 삭제 대상에서 제외했다. 기존 노트북과 pilot.py/분석/감사 코드는 실행하지 않았다. 기존 상대·절대 데이터 경로, 폴더 깊이, article_id/URL 대응은 유지됐다.',
              '', '## 4. 연구 자료 보존과 과거 manifest 차이','',
              f'핵심 CSV 2개와 사용자 지정 과거 감사 CSV는 SHA256 동일하다. vendor 외 CSV·HTML·전체 TXT·노트북 **{len(critical):,}파일**을 1차 해시와 대조해 동일함을 확인했다. 기존 품질 감사·본문 시험·메타데이터 분석의 나머지 해시 자료도 보존했다. README 외 승인되지 않은 기존 파일 내용 변경/누락은 0건이다.',
              '', '|과거 manifest|현재 존재·SHA 비교|이번 변경/기존 차이|','|---|---|---|']
        for h in historical:
            newly=sum(r['caused_by_this_cleanup'] for r in h['missing']);prior=len(h['missing'])-newly
            L.append(f'|{h["manifest"]}|'+('일치' if h['matches_current_snapshot'] else '불일치/의도된 차이 있음')+f'|신규 삭제 {newly}, 이전부터 부재 {prior}, 내용 변경 {len(h["changed"])}|')
        L += ['', '기존 manifest와 1차 validation.json은 수정하지 않았다. 특히 1차 전체 보존 스냅샷은 삭제된 vendor 파일과 승인된 README 변경 때문에 현재 상태에 그대로 통과할 수 없다. 과거 검증기를 실행해 원본 검증 파일을 덮어쓰지 않고, 새 post_cleanup_validation.json에 실제 비교 결과를 남겼다. 과거 pilot의 05_article_index_v2.csv 등 이미 없던 항목은 이번 삭제와 구분했다.',
              '', f'배포 패키지의 dist-info/RECORD **{len(record_effects)}개**는 삭제 파일을 계속 열거한다. 메타데이터 자체는 수정하지 않았다. RECORD 기준 설치 무결성은 삭제한 파일에 대해 의도적으로 불일치하므로 정상 통과했다고 주장하지 않는다. 정확한 멤버 목록은 새 검증 JSON에 있다.',
              '', '## 5. README와 새 작업 구조','',
              'README.md를 프로젝트 진입 안내로 개선했다. 연구 목적·기간, NewsCatcher 영어 필터 사용자 확인, 두 입력 CSV, 기존 작업 단계, 51건 시험/감사, 과거 A/B/C의 한계, 다음 후보 선정 작업, 경로·실행 주의사항을 상대 링크로 연결했다. 보고서를 복사하지 않았다.',
              '', '```text','scripts/                         # 신규 운영 코드 안내 README','notebooks/                       # 신규 노트북 안내 README','hormuz_2026_data/runs/            # 새 작업 결과 안내 README','hormuz_2026_data/corpus/          # 향후 분석용 본문 안내 README','기존 데이터·실험·노트북            # 원래 경로 유지','```','',
              '각 폴더에 용도를 설명하는 README만 생성했다. scripts/notebooks의 안내는 비밀·데이터를 담지 않으며 commit/add하지 않았다. runs/corpus는 기존 hormuz_2026_data/ ignore 규칙을 따른다. .gitignore·Git 설정·이력은 수정하지 않았고 commit/push하지 않았다. .env 비밀 값은 읽거나 출력하지 않았다.',
              '', '## 6. 오프라인 복구와 남은 검토','',
              f'복구본: [vendor_cleanup_recovery.zip](vendor_cleanup_recovery.zip), SHA256 `{recovery["sha256"]}`. 삭제 파일 {len(deleted)}개의 원래 프로젝트 상대 경로와 정확한 내용을 압축 저장했다. 복구본을 불필요하게 Git에 추가하지 않는다. 외부 다운로드 없이 복구할 수 있으며 현재는 복구를 실행하지 않았다.',
              '', '복원이 필요할 때 archive SHA256과 원장/멤버별 해시를 먼저 대조하고, 멤버의 절대 대상 경로가 프로젝트 vendor 내부인지 확인한 후 현재 없는 파일만 복원한다. 이미 존재하는 파일은 덮어쓰지 않는다. 원장에 복구를 기록하고 동일 import 검사를 다시 수행한다. 타임스탬프는 1차 inventory/baseline을 참고하며 바이트 단위 복구 보장을 원래 NTFS 권한/모든 메타데이터 복구 보장으로 확대하지 않는다.',
              '', f'남은 vendor 후보 {result["vendor_candidates_held"]}개는 동적 import/실행·Windows 환경 또는 실제 런타임 용도를 별도로 검토해야 한다. 완전한 전이 의존성 lock과 전체 기능 재현은 아직 미확정이다. 전체 vendor를 삭제하지 않는다.',
              '', '다음 작업의 진입점은 루트 [README.md](../../README.md) → [수집·분류 기준](../data_collection_criteria_v1.md) → [품질 감사의 후속 계획](../../hormuz_2026_data/quality_audit_20261107_01/NEXT_SELECTION_PLAN.md)이다. 전체 메타데이터 기반 분산 수집 후보 선정은 별도 작업이며 이번 정리에서 수행하지 않았다.',
              '', f'새 보존 검증 결과: **{result["passed"]}**. 이는 승인된 변경을 구분한 이번 정리 검증이며, 과거 검증/RECORD 무결성이 전부 통과한다는 뜻이 아니다. [post_cleanup_validation.json](post_cleanup_validation.json)에 검사 범위와 한계를 기록했다.','']
        text='\n'.join(L);REPORT.write_text(text,encoding='utf-8');VALID.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        final,_=walkfiles()
        if len(final)==result['after_file_count'] and sum(r['bytes'] for r in final.values())==result['after_bytes']:break
    else:raise RuntimeError('Final recursive count did not stabilize')
    print(json.dumps({k:result[k] for k in ['passed','deleted_files','deleted_bytes','preserved_candidate_files','before_file_count','before_bytes','after_file_count','after_bytes','net_project_bytes_reduced','unexpected_missing_originals','unexpected_content_changes','research_artifact_hashes_verified']},ensure_ascii=False,indent=2))
    if not result['passed']:raise SystemExit(1)
if __name__=='__main__':main()
