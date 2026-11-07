"""Freeze 200 existing-ready rows and prepare/test the runner WITHOUT networking."""
from pathlib import Path
from collections import Counter
from unittest.mock import patch
import argparse,csv,json,os,time,sys,io,unittest,contextlib,warnings
import pandas as pd
import body_pilot_200 as pilot

ROOT=Path(__file__).resolve().parents[1];DATA=ROOT/'hormuz_2026_data'
SOURCE=DATA/'runs/candidate_selection_20261107_01'
OLD=DATA/'body_collection_pilot_20261102_01';QA=DATA/'quality_audit_20261107_01'
NEW_SCRIPTS={'body_pilot_200.py','test_body_pilot_200.py','prepare_body_pilot_200.py','inspect_pilot_200_sources.py','find_newscatcher_query_evidence.py'}
def log(out,stage,**kw):
    row=dict(stage=stage,elapsed_sec=round(time.perf_counter()-START,3),**kw)
    with (out/'preparation_progress.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    print(json.dumps(row,ensure_ascii=False),flush=True)
def main():
    global START;START=time.perf_counter()
    a=argparse.ArgumentParser();a.add_argument('--run-dir',type=Path,required=True);args=a.parse_args();out=args.run_dir.resolve()
    if not out.is_relative_to(DATA/'runs') or not out.name.startswith('body_pilot_200_'):raise ValueError('Dedicated pilot run required')
    out.mkdir(parents=True,exist_ok=True)
    if set(p.name for p in out.iterdir())-{'PLAN.md','newscatcher_query_evidence.json'}:raise FileExistsError('Refuse overwrite of prepared/partial run')
    if not (out/'newscatcher_query_evidence.json').exists():raise FileNotFoundError('Inspect local notebook/Git search settings first')
    token=pilot._NO_NETWORK.set(True)
    protected={};metadata={}
    try:
        for base,dirs,files in os.walk(ROOT):
            dirs[:]=[d for d in dirs if d not in {'.git','vendor','__pycache__'} and not (Path(base)/d).resolve().is_relative_to(out)]
            for n in files:
                p=Path(base)/n
                if n=='.env' or n.startswith('.env.') or (p.parent==ROOT/'scripts' and n in NEW_SCRIPTS):continue
                r=p.relative_to(ROOT).as_posix();st=p.stat();protected[r]=pilot.sha(p);metadata[r]=(st.st_size,st.st_mtime_ns)
        pilot.atomic_json(out/'preservation_before.json',dict(hashes=protected,stat={p:list(s) for p,s in metadata.items()},scope='existing nonsecret/nonGit/nonvendor project files; new run/current-task scripts excluded'))
        source_manifest=pilot.jread(SOURCE/'manifest.json');source_validation=pilot.jread(SOURCE/'validation.json')
        if not source_validation['passed']:raise ValueError('Existing candidate selection validation failed')
        for r in source_manifest['outputs']+source_manifest['code']:
            if protected.get(r['path'])!=r['sha256']:raise ValueError('Existing source manifest mismatch:'+r['path'])
        log(out,'existing_inputs_verified',protected_files=len(protected),source_queue_rows=source_validation['queue_rows'])
        previous=pilot.csvread(OLD/'selection.csv');oldids={r['article_id'] for r in previous};oldurls={r['url'] for r in previous}
        # All previously attempted benchmark identities are also respected if
        # a collision is encountered, without changing the source ready queue.
        benchmark=DATA/'06_benchmark_fetch_log.csv'
        if benchmark.exists():
            for r in pilot.csvread(benchmark):
                if r.get('article_id'):oldids.add(r['article_id'])
                if r.get('url'):oldurls.add(r['url'])
        selected=[];replacements=[];first200=[]
        with (SOURCE/'candidate_queue.csv').open(encoding='utf-8-sig',newline='') as f:
            for position,row in enumerate(csv.DictReader(f),1):
                if position<=200:first200.append(row)
                why=[]
                if row['article_id'] in oldids or row['request_url'] in oldurls:why.append('previously_requested_pilot_or_benchmark_URL')
                if row['article_id'] in {r['article_id'] for r in selected} or row['request_url'] in {r['request_url'] for r in selected}:why.append('duplicate_in_trial')
                try:
                    info=pilot.URLGuard().check(row['request_url'],resolve=False)
                    if info['host']!=row['domain']:why.append('metadata_request_host_mismatch')
                except pilot.PolicyBlocked as e:why.append(str(e))
                if why:replacements.append(dict(skipped_distributed_order=row['distributed_order'],article_id=row['article_id'],reason=';'.join(why),replacement_distributed_order=''));continue
                row=dict(row,pilot_order=str(len(selected)+1));selected.append(row)
                if len(selected)==200:break
        if len(selected)!=200:raise ValueError('Insufficient validated existing ready rows')
        used={r['article_id'] for r in selected}
        original_requests={r['article_id']:r['request_url'] for r in selected}
        # A replacement is the next retained source row beyond the initial200;
        # no domain/month/country-driven replacement or target is used.
        extras=[r['distributed_order'] for r in selected if int(r['distributed_order'])>200]
        for r,new in zip(replacements,extras):r['replacement_distributed_order']=new
        details={};cols=['article_id','url','url_raw','candidate_record_number','published_date','month','domain','nc_source_name','nc_country','raw_NA_country_evidence','region','country_basis','nc_language','nc_paid_content','topic_evidence','type_hint','technical_risk','domain_pilot_caution','source_review_hint','title_dup_group_id','title_dup_group_size','nc_url_record_count','selection_status','distributed_order']
        for c in pd.read_csv(SOURCE/'candidate_screening.csv',usecols=cols,dtype=str,keep_default_na=False,encoding='utf-8-sig',chunksize=20000):
            for r in c.loc[c.article_id.isin(used)].to_dict('records'):details[r['article_id']]=r
            if len(details)==200:break
        titles={}
        for c in pd.read_csv(DATA/'05_extraction_candidates_v2.csv',usecols=['article_id','title'],dtype=str,keep_default_na=False,encoding='utf-8-sig',chunksize=20000):
            titles.update(dict(zip(c.loc[c.article_id.isin(used),'article_id'],c.loc[c.article_id.isin(used),'title'])))
            if len(titles)==200:break
        for r in selected:
            d=details[r['article_id']]
            if d['selection_status']!='ready' or d['distributed_order']!=r['distributed_order'] or d['domain']!=r['domain'] or d['published_date']!=r['published_date']:raise ValueError('Screening/source queue correspondence mismatch')
            r.update({k:d[k] for k in cols if k!='article_id'});r['title']=titles[r['article_id']]
            r['selection_reason']='existing_distributed_ready_prefix;no_month_country_media_quota'
            # Source queue runtime state is preserved as metadata, actual state
            # lives only in pilot_state.csv from this point onward.
        pilot.csvwrite(out/'pilot_200_queue.csv',selected)
        pilot.csvwrite(out/'pilot_selection_replacements.csv',replacements,['skipped_distributed_order','article_id','reason','replacement_distributed_order'])
        config=pilot.default_config();config['source_queue_sha256']=pilot.sha(SOURCE/'candidate_queue.csv');config['selection_method']='first200_existing_distributed_ready_queue;only documented deterministic defect/duplicate replacement'
        config['extractor_settings']=dict(trafilatura_default='json with_metadata=True include_comments=False; favor_precision=False',trafilatura_precision='same HTML; favor_precision=True',newsplease='from_html same saved HTML; fetch_images=False; network/file writes denied; conditional fallback, never longest')
        versions={}
        if str(pilot.VENDOR) not in sys.path:sys.path.insert(0,str(pilot.VENDOR))
        for name in ['trafilatura','news-please','langdetect','beautifulsoup4','newspaper4k']:
            try:versions[name]=pilot.importlib.metadata.version(name)
            except pilot.importlib.metadata.PackageNotFoundError:versions[name]='unavailable'
        config['installed_versions']=versions;config['python_version']=sys.version;pilot.atomic_json(out/'pilot_config.json',config)
        store=pilot.StateStore(out,selected);store.initialize()
        for d in ['html','texts','results','robots']: (out/d).mkdir(exist_ok=True)
        lock=dict(immutable_hashes={n:pilot.sha(out/n) for n in ['pilot_200_queue.csv','pilot_config.json']},code_hashes={'scripts/body_pilot_200.py':pilot.sha(ROOT/'scripts/body_pilot_200.py')})
        pilot.atomic_json(out/'pilot_lock.json',lock)
        policies={d:dict(terms_reviewed=False,allowed=False,evidence='') for d in sorted({r['domain'] for r in selected})}
        pilot.atomic_json(out/'execution_approval_TEMPLATE.json',dict(approved=False,approved_by='',approved_at='',article_count=200,queue_sha256=pilot.sha(out/'pilot_200_queue.csv'),domain_policies=policies,
            note='NOT authorization. Only fill/copy after later explicit user approval and per-domain terms review. robots checked only during actual authorized execution.'))
        log(out,'fixed200_selected',months=dict(sorted(Counter(r['month'] for r in selected).items())),domains=len(policies),replacements=len(replacements))
        # Offline tests use synthetic memory HTML and temporary fixture stores.
        stream=io.StringIO()
        suite=unittest.defaultTestLoader.discover(str(ROOT/'scripts'),pattern='test_body_pilot_200.py')
        with warnings.catch_warnings(record=True) as caught,patch('socket.getaddrinfo',side_effect=AssertionError('Preparation forbids DNS')),patch('socket.create_connection',side_effect=AssertionError('Preparation forbids network')):
            tests=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
        (out/'mock_test_results.log').write_text(stream.getvalue(),encoding='utf-8')
        pilot.atomic_json(out/'mock_test_warnings.json',[dict(category=w.category.__name__,message=str(w.message),filename=str(w.filename),line=w.lineno) for w in caught])
        # Validate default executable entry path, with its network guard active.
        buf=io.StringIO();saved_argv=sys.argv[:]
        try:
            sys.argv=['body_pilot_200.py','--run-dir',str(out)]
            with contextlib.redirect_stdout(buf),patch('socket.getaddrinfo',side_effect=AssertionError('Dry-run DNS forbidden')),patch('socket.create_connection',side_effect=AssertionError('Dry-run network forbidden')):pilot.main()
        finally:sys.argv=saved_argv
        dry=json.loads(buf.getvalue());pilot.atomic_json(out/'dry_run_result.json',dry)
        pilot.atomic_json(out/'development_verification_notes.json',dict(expected_TDD_initial_failure='ModuleNotFoundError before crawler existed',
            corrected_before_final_tests=['keyword counting overlapped across phrase boundaries; now sentence-separated and nonoverlapping','robots Request-rate was initially not enforced; added conservative seconds/requests interval','orphan HTML initially entered refetch path; now review without request','older failed checkpoint could shadow later interrupted attempt; match attempts and terminal state','retry result JSON initially retained older attempts; now updated per attempt'],
            independent_review_fixes=['absolute response deadline now includes connect/headers and read1 body; socket shutdown watchdog interrupts slow responses','429/5xx Retry-After retained in full; above auto retry window becomes manual review','offline guard covers all socket audit events','newspaper first-import tempfile write-probe avoided using an existing directory; extractor writes remain denied'],
            environment_test_failure='Default sandbox could not read vendor dependency files. Read-authorized rerun used existing packages only, without downloads.',
            note='All final mock-test outcomes are in mock_test_results.log; deprecation warnings are retained separately. No actual article was used as a fixture.'))
        # Fresh before/after comparisons include original large CSVs; do not
        # conflate earlier historical manifest changes with this preparation.
        changes=[];after_hashes={}
        for r,h in protected.items():
            p=ROOT/r
            if not p.is_file():changes.append(r);continue
            after_hashes[r]=pilot.sha(p)
            if after_hashes[r]!=h or (p.stat().st_size,p.stat().st_mtime_ns)!=metadata[r]:changes.append(r)
        log(out,'offline_tests_and_preservation',tests=tests.testsRun,test_failures=len(tests.failures),test_errors=len(tests.errors),changed_old_files=changes)
        months=dict(sorted(Counter(r['month'] for r in selected).items()));domains=Counter(r['domain'] for r in selected)
        orig51ids={r['article_id'] for r in previous};orig51urls={r['url'] for r in previous}
        checks=dict(exact200=len(selected)==200,all_in_existing_ready_queue=all(details[r['article_id']]['selection_status']=='ready' for r in selected),
            no_ID_or_request_URL_duplicates=len({r['article_id'] for r in selected})==200 and len({r['request_url'] for r in selected})==200,
            existing51_no_overlap=not (used&orig51ids) and not ({r['request_url'] for r in selected}&orig51urls),
            all_8_months=set(months)=={f'2026-{m:02}' for m in range(1,9)},source_order_monotonic=[int(r['distributed_order']) for r in selected]==sorted(int(r['distributed_order']) for r in selected),
            exact_first200_or_documented_replacements=[r['article_id'] for r in selected]==[r['article_id'] for r in first200] if not replacements else bool(replacements),
            no_whitelist_or_quota=config['selection_method'].startswith('first200_existing'),all_initial_state_pending=all(r['status']=='pending' and r['attempts']=='0' for r in store.load()),
            queue_and_country_literals_preserved=all(r['nc_country']==details[r['article_id']]['nc_country'] and r['url']==details[r['article_id']]['url'] and r['request_url']==original_requests[r['article_id']] for r in selected),
            no_actual_HTML_text_result_robots_files=not any(list((out/d).iterdir()) for d in ['html','texts','results','robots']),
            mock_tests_passed=tests.wasSuccessful(),default_entrypoint_is_offline=dry['HTTP_requests']==0 and dry['URL_count']==200,
            all_old_research_files_unchanged=not changes,source_ready_queue_not_changed=after_hashes[(SOURCE/'candidate_queue.csv').relative_to(ROOT).as_posix()]==config['source_queue_sha256'])
        val=dict(passed=all(checks.values()),checks=checks,selected_URLs=200,months=months,domain_count=len(domains),top_domains=domains.most_common(10),top1_share=max(domains.values())/200,top10_share=sum(n for d,n in domains.most_common(10))/200,
            substitutions=replacements,original51_overlap=0,original_queue_count_reused=source_validation['queue_rows'],parent_states_reused=source_validation['states'],
            mock_tests=dict(run=tests.testsRun,failures=len(tests.failures),errors=len(tests.errors),real_article_successes_counted=0),
            protected_files=len(protected),source_changes=changes,actual_original_CSV_hashes={r:dict(before=protected[r],after=after_hashes.get(r)) for r in ['hormuz_2026_data/01_newscatcher_raw.csv','hormuz_2026_data/05_extraction_candidates_v2.csv']},
            installed_versions=versions,HTTP_requests=0,robots_requests=0,HTML_downloads=0,actual_news_body_extractions=0,synthetic_fixture_extractions=True,
            total_preparation_sec=round(time.perf_counter()-START,3),live_approval_granted=False,
            limitations=['DNS public-IP and site terms/robots of actual200 domains are NOT checked yet','Synthetic extraction tests do not prove real article extraction quality','Historical API code settings not uniquely tied to the current raw CSV by an original collection-run manifest'])
        pilot.atomic_json(out/'validation.json',val)
        write_docs(out,selected,val)
        plan=out/'PLAN.md'
        if val['passed']:plan.write_text(plan.read_text(encoding='utf-8').replace('- [ ]','- [x]'),encoding='utf-8')
        oldinputs=[SOURCE/n for n in ['candidate_queue.csv','candidate_screening.csv','validation.json','config.json','manifest.json','SELECTION_DESIGN_REPORT.md','distribution_check.csv']]+[OLD/'selection.csv',OLD/'pilot.py',OLD/'REPORT.md',QA/'quality_audit.csv',QA/'QUALITY_AUDIT_REPORT.md',QA/'NEXT_SELECTION_PLAN.md',ROOT/'README.md',ROOT/'docs/data_collection_criteria_v1.md',DATA/'01_newscatcher_raw.csv',DATA/'05_extraction_candidates_v2.csv',*ROOT.glob('*.ipynb')]
        code=[ROOT/'scripts'/n for n in sorted(NEW_SCRIPTS)]
        manifest=dict(task='fixed200 pilot PREPARATION ONLY',inputs=[dict(path=p.relative_to(ROOT).as_posix(),sha256=protected[p.relative_to(ROOT).as_posix()],bytes=p.stat().st_size) for p in oldinputs],
            code=[dict(path=p.relative_to(ROOT).as_posix(),sha256=pilot.sha(p)) for p in code],outputs=[dict(path=p.relative_to(ROOT).as_posix(),sha256=pilot.sha(p),bytes=p.stat().st_size) for p in out.iterdir() if p.is_file() and p.name!='manifest.json'],
            locked_runtime_inputs=lock,mutable_after_later_execution=['pilot_state.csv','html/','texts/','results/','robots/','events.jsonl','network_policy_state.json','request_activity.json'],
            validation_passed=val['passed'],HTTP_requests_this_preparation=0,actual_article_files_created=0,separate_live_authorization_required=True)
        pilot.atomic_json(out/'manifest.json',manifest)
        print(json.dumps({k:val[k] for k in ['passed','selected_URLs','months','domain_count','top1_share','top10_share','mock_tests','installed_versions','total_preparation_sec']},ensure_ascii=False,indent=2))
        if not val['passed']:raise SystemExit(1)
    finally:pilot._NO_NETWORK.reset(token)

def write_docs(out,rows,v):
    evidence=pilot.jread(out/'newscatcher_query_evidence.json')
    summary=dict(queries=['"Strait of Hormuz"','"Hormuz Strait"'],lang='en',source='local Git bb85a6cccf7e7eaa3fabaf5488ccad367aaf7123:hormuz_2026_news_textmining_filtered.ipynb',
        locations=dict(global_conditions='cell6 lines34-40',test_request='cell8 lines1-12, page_size=1',collection_request='cell12 lines48-65 q=q lang=en from_=from_date to_=to_date sort_by=date',collection_loop='cell12 line160 for q in QUERIES',date_split='cell12 lines123-136 pd.Timestamp midpoint; right_from=mid+1day; %Y-%m-%d'),
        search_fields='search_in not explicitly supplied in located code; API/SDK default fields UNKNOWN',operator='two separately iterated double-quoted phrase strings; no explicit Boolean OR/AND found',
        start_date='2026-01-01',end_date='2026-08-31',API_boundary_timezone_semantics='date strings without explicit timezone; remote inclusivity/default field semantics not verified offline',
        provenance_limit='Located historical collection code, but exact revision/query-to-current-CSV run binding unavailable. English filter independently user-confirmed.')
    pilot.atomic_json(out/'newscatcher_query_summary.json',summary)
    L=['# 200개 시험 URL 선정 결과','', '**준비만 수행했다. 실제 기사·robots HTTP 요청, HTML 다운로드, 실제 뉴스 본문 추출은 0회다.**','',
       '모집단은 기존 ready 큐 125,948개뿐이다. 427,875개를 재분류하거나 기존 큐를 재생성하지 않았다. 기존 집계/검증과 원본 SHA를 재사용·대조했으며 월/국가/매체 전체 분포를 다시 계산하지 않았다.',
       '', f'선정 **200개**, 도메인 **{v["domain_count"]}개**, 가장 큰 도메인 비중 **{v["top1_share"]:.1%}**, 상위10 합계 **{v["top10_share"]:.1%}**. 기존 고정51건과 ID/기존 URL 중복은 0건이다. 과거 요청 benchmark도 중복 방지 검사에 포함했다.',
       '', f'대체 **{len(v["substitutions"])}건**. 기본안인 분산 큐 앞200개를 유지했다.' if not v['substitutions'] else f'대체 {len(v["substitutions"])}건; pilot_selection_replacements.csv에 제외 순번·사유·새 순번 기록.',
       '', '|UTC 월|선정 URL|전체 ready 건수(기존 집계)|','|---|---:|---:|']
    source=pilot.jread(SOURCE/'validation.json')
    for m,n in v['months'].items():L.append(f'|{m}|{n}|{source["months"][m]["ready"]:,}|')
    L+=['','|상위 도메인|건수|','|---|---:|']
    for d,n in v['top_domains']:L.append(f'|{d}|{n}|')
    L+=['','월별 균등 배분·국가 최소 수량·매체 whitelist는 없다. 기존 분산 순서를 그대로 사용했으며 첫8개 각 월 warmup 때문에 작은 월은 전체 ready 비중보다 초반에서 상대적으로 커질 수 있다. 이는 기술 시험 순서이지 통계적 대표 표본이 아니다. 172개 도메인을 172개 독립 원보도 언론사로 해석하지 않는다. news-pravda.com 등 재게시 가능성이 있는 출처도 후속 출처 검토가 필요하지만 모집단 밖 URL로 대체하지 않았다.',
         '', '## NewsCatcher 실제 검색 설정 근거','',
         '현재 실제 노트북 이름은 hormuz_textminig.ipynb, hormuz_textminig_v2.ipynb다. 두 파일은 cell1에서 NewsCatcher 재수집 제거를 명시한다. 존재하지 않는 유사 철자 파일을 실행하지 않았다.',
         '', '로컬 Git 이력에서 `bb85a6cccf7e7eaa3fabaf5488ccad367aaf7123:hormuz_2026_news_textmining_filtered.ipynb`의 수집 코드를 확인했다. QUERIES는 **`\"Strait of Hormuz\"`**, **`\"Hormuz Strait\"`** 두 문자열이다(cell6 line37 이하). cell12 line160에서 각 q를 순회하고, lines55-64에서 q, lang=en, from_=from_date, to_=to_date, sort_by=date를 전달한다. 별도의 OR/AND 또는 search_in 필드는 확인되지 않았다. 호출 대상 필드/API 기본 검색 범위를 추측해 title-only/full-content라고 단정하지 않는다.',
         '', '기간 상수는 2026-01-01~08-31(cell6 lines34-35)이다. cell12 lines123-136은 페이지 제한 때 pd.Timestamp로 중간 날짜를 구하고 오른쪽 구간을 mid+1day로 시작해 %Y-%m-%d로 전달한다. 명시적 시간대는 없다. 원격 API의 날짜 경계/기본 필드 동작은 외부 접속 없이 확인할 수 없다. 이번 후보 발행일은 기존의 naive→UTC 해석을 변경하지 않았다.',
         '', '**찾은 것은 로컬 과거 수집 코드의 설정이다. 현재 원본 CSV가 정확히 이 revision/검색식으로 생성됐다는 원 수집 run manifest 연결은 확인 불가다.** 영어 필터는 사용자 확인과 과거 코드 lang=en 양쪽 근거가 있다. 근거 파일·Git blob·cell/line·안전한 설정만 newscatcher_query_evidence.json과 summary에 남겼고 키·환경값·전체 비밀 코드/출력을 복사하지 않았다. Git show/log만 읽었으며 API 호출은 하지 않았다.',
         '', '## 보존','', f'기존 파일 {v["protected_files"]:,}개(비밀/Git/vendor 제외)의 실제 해시·수정 시각을 전후 대조해 변경 0건이다. 두 원본 CSV 전체 SHA도 실제 읽어 기록했다. 기존 보고서·51건 자료·원본 큐는 변경하지 않았다. 국가 nc_country와 raw_NA_country_evidence는 원래 값 그대로 별도 보존했다. 선정 title은 기존 후보 CSV에서 article_id로 필요한 200개만 읽었다.','']
    (out/'pilot_selection_report.md').write_text('\n'.join(L),encoding='utf-8')
    plan=['# 200개 시험 실제 실행 계획 — 현재는 실행 승인 없음','',
          '향후 활용 가능한 영어 본문 20,000건 이상은 사용자 전달 최소 목표다. 이번에는 고정200 URL의 기술적 가능성을 시험할 준비만 했다. 200개의 성공률을 전체 원자료의 확보율로 일반화하거나 고정 연구 가설을 만들지 않는다.',
          '', '## 실행 전','',
          '1. 별도 사용자 실행 승인을 받는다. 준비 승인을 실제 요청 승인으로 재해석하지 않는다. 큐/config/runner hash를 pilot_lock.json과 비교한다.',
          '2. execution_approval_TEMPLATE.json은 approved=false인 비승인 양식이다. 이후 명시 승인에 근거해 별도 approval 파일을 만들고 approved_by/approved_at(시간대 포함)/큐 SHA/정확히200개를 기록한다. 양식 자체는 승인으로 쓰지 않는다.',
          '3. 각 도메인 이용 조건/접근 정책을 검토해 domain_policies의 terms_reviewed, allowed, evidence를 기록한다. robots 허용은 이용 조건 검토·법적 사용 허가와 다르다. 미확인은 요청 전에 review이며 robots 요청도 하지 않는다. 리디렉션으로 다른 도메인이 등장하면 그 도메인도 별도 근거가 필요하다.',
          '4. 기본 실행은 dry-run이며 DNS·기사·robots 요청이 없다. 실제 실행에는 --execute와 --approval-file이 모두 필요하다. 현재 실행하지 않았다.',
          '', '## 실제 요청 시 동작','',
          '- 동시성1, 기본 도메인 최소2초, robots Crawl-delay/Request-rate가 더 보수적이면 그 값을 적용한다. robots 요청도 같은 rate limiter를 사용한다. 원본 URL의 scheme/host/port/credential/query를 검사하고 DNS의 모든 IP가 public인지 확인한다. localhost·사설/링크로컬·내부/예약 호스트·혼합 DNS·자격증명 URL은 차단한다.',
          '- 검증 IP에 연결을 고정해 DNS 재해석을 피하고 HTTPS 원래 호스트의 SNI/인증서 검증을 유지한다. 프록시 환경·인증·쿠키 jar를 사용하지 않는다. 명확한 User-Agent를 사용한다. HTTP로 주어진 URL을 임의로 HTTPS로 바꾸지 않으며 HTTPS→HTTP 리디렉션은 차단한다.',
          '- 각 리디렉션도 최대5회까지 안전성·도메인 이용 조건·robots를 확인한다. 기사 내부 링크·canonical·추천 URL을 새 기사로 추가하지 않는다. 기사 입력 allowlist는 잠긴200개뿐이다. robots와 같은 기사의 제한된 redirect는 접근 단계이며 200개 외 새 기사 표본이 아니다.',
          '- robots 404/410은 지시 없음으로 처리하되 이용 조건은 여전히 필요하다. robots 401/403은 차단, 5xx·429는 제한 재시도/쿨다운, HTML로 대체된 robots·불명 상태는 review다. 리디렉션된 robots는 같은 origin만 허용한다. 매 실행 robots를 다시 확인하고 저장 응답을 근거로 남긴다.',
          '- 연결10초, 읽기30초, 응답 누적60초 deadline. HTML 8MB·robots512KB 제한, Content-Type/HTML signature 확인. Accept-Encoding=identity; 인코딩/압축 예외는 review해 압축폭탄/부분 HTML을 추출하지 않는다.',
          '- 401/403은 우회·자동 재시도 없이 policy_blocked다. 429는 Retry-After와 쿨다운을 적용한다. 5xx/DNS/연결/timeout만 최대2재시도(총3 logical attempts), 지수 백오프. 도메인3회 실패 시 기본900초 circuit breaker. 한 실행은200개의 한 pass뿐이며 due 전 retry를 기다리며 반복하지 않는다.',
          '', '## 중단·재개','',
          'pilot_state.csv는 article_id/시험순번/URL/domain/status/attempts/last_request_at/next_retry_at/http_status/extractor/error_type/html_path/result_path를 저장한다. pending은 미처리, running은 처리 중, done은 프로그램 완료(사람 정답 아님), review는 정책/품질/접근 불확실, error는 기술 실패, policy_blocked는 금지/장벽이다. logical attempts와 실제 HTTP response activity를 구분한다.',
          '', 'done/review/policy_blocked는 자동 재요청하지 않는다. error는 다음 retry 시각·횟수가 허용된 경우만 재시도한다. 중단 당시 running은 현재 attempt와 맞는 terminal 결과+해시가 있으면 복구하고, 검증된 HTML이 있으면 추출만 재개한다. 요청 후 파일이 없거나 이전 attempt 결과만 있으면 review로 보내 중복 refetch를 하지 않는다. orphan HTML/provenance도 review다.',
          '', '실행 lock이 있으면 두 프로세스를 동시에 실행하지 않는다. 비정상 종료 lock은 PID가 실제 실행 중인지 사람이 확인한 뒤 이 새 run의 lock만 제거한다. 기존 원본 파일을 제거하지 않는다. 프로세스 running 기록 복구 후 새 한 pass를 실행한다. queue/config는 고정하고 상태/로그/결과만 이 run에서 갱신한다.',
          '', '민감한 headers, API 키, 비밀번호, 인증 토큰, 쿠키를 요청/저장/로그에 사용하지 않는다. 원문 공개 HTML에는 사이트 제공 공개 코드/문구가 있을 수 있으므로 저장 HTML도 비공개 연구 자료로 취급한다. 저장 HTML과 본문은 각각 html/, texts/, 메타데이터/판정은 results/, 정책 근거는 robots/, 상태·오류는 CSV/JSONL로 구분한다.','']
    (out/'PILOT_EXECUTION_PLAN.md').write_text('\n'.join(plan),encoding='utf-8')
    quality=['# 본문 품질·언어·관련성·중복 프로토콜','',
             '주 결과는 trafilatura 기본(댓글 제외)이며 같은 저장 HTML로 precision도 비교한다. 기본이 비거나 짧음·언어/키워드·오염·제목·모드 차이가 의심되면 news-please를 보조로 실행한다. 모든 결과의 상태/오류/버전/글자·단어 수/파일 경로를 보존한다. 가장 긴 본문을 자동 채택하지 않는다. 기본이 비고 보조만 있으면 provisional fallback으로 review한다.',
             '', '표시된 paywall/login container의 명시적 계속 읽기 문구는 gate 상태로 분리하고 본문을 추출하지 않는다. schema isAccessibleForFree=false 또는 구조 확인 없는 gate 문구는 의심/review로 보존하며 paid 확정 사실로 바꾸지 않는다. 일반 newsletter/구독 홍보만으로 유료라고 판단하지 않는다. no_gate_detected도 전체 무료 본문 확인이 아니다. 일부/전체 유료 구분은 saved HTML만으로 단정하지 않고 paid_or_partial_gate_detected로 표시한다.',
             '', '## 기록 지표','',
             '- HTTP 접근/HTML 저장/본문 추출 성공은 별개다. HTTP 실패에는 추출 성공이나 언어 false를 대신 기록하지 않는다. 빈 추출문과 not_run·extractor_error를 구분한다. 본문 없는 접근/장벽 결과에는 언어·품질가 미확정임을 명시한다.',
             '- langdetect seed0, 처음10,000자, 최소 알파벳30자. en/other/unknown과 추정 확률을 기록한다. 이 값은 보정된 신뢰도·사람 정답이 아니다. en 미확정 또는 확률0.8 미만은 review다.',
             '- NFKC·대소문자·일반 공백/구두점 변형을 정규화해 Strait of Hormuz, Hormuz Strait, Straits of Hormuz, Hormuz Straits를 비중첩 집계한다. Hormuz 총 횟수와 표현 밖 단독 횟수, 제목/본문 포함 여부를 따로 기록한다. 문장 경계를 넘어 구절을 잘못 잇지 않는다.',
             '- 표현이 있으면 explicit_strait_mention, Hormuz만 있으면 hormuz_only, 없으면 no_body_keyword. 단독/미포함은 문맥 검토이며 삭제·HTTP 실패로 바꾸지 않는다. AI 주제 분류/새 의미 점수는 도입하지 않는다.',
             '- 빈 본문, 80단어 미만은 review 신호다. 짧은 Urgent/속보도 있을 수 있으므로 길이 하나로 제외하지 않는다. comments/recommended stories/related articles/newsletter UI 패턴과 메타데이터-추출 제목 토큰 Jaccard는 의심 플래그일 뿐 오염/불일치 확정이 아니다.',
             '- 기존 title_dup_group_id/size와 nc_url_record_count를 그대로 보존한다. 제목만 같다고 삭제하지 않는다. 전체 본문 raw SHA256, 공백 정리 SHA256으로 동일 후보를 표시하고, 80단어 이상은 단어 빈도 SimHash64 거리<=3으로 매우 유사 가능성을 표시한다. 이는 의미/통신사 전재 정답이 아니며 원문은 유지한다.',
             '- 이전 결과와 중복이면 새 결과에 previous IDs를 남긴다. 이 플래그는 최초 결과에 역방향으로 자동 정답을 부여하지 않으며 전체 중복 그룹은 결과 검토 시 결합한다. 본문을 자동 삭제하거나 합치지 않는다.',
             '', 'done/automated_checks_passed_human_unconfirmed는 실행 완료와 자동 진단 통과만 뜻한다. 모든 결과 human_review_required=true, human_final_label은 비워 둔다. 제목-본문 대응, 앞/중간/끝 누락, 원보도/재게시·번역, 정상 기사/목록/영상/라이브, 실제 무료 전체 본문은 사람 또는 별도 증거로 확인한다.',
             '', 'Iran International 타기사 혼입, MercoPress 댓글, Philstar 메뉴, Premium Times/Al Jazeera 홍보 삽입 등 이전51건 오류를 자동으로 해결했다고 주장하지 않는다. 새 사이트 DOM 규칙을 검증 없이 하드코딩하지 않는다. DOM/키워드/제목 의심과 모드 차이를 검토에 올리고 결과를 임의 교체하지 않는다.',
             '', '이번 mock 테스트는 합성 HTML만 사용했으며 실제 사이트 성공률에 포함하지 않았다. 준비 단계의 html/texts/results/robots 폴더는 비어 있다.','']
    (out/'PILOT_QUALITY_PROTOCOL.md').write_text('\n'.join(quality),encoding='utf-8')
    rel=out.relative_to(ROOT).as_posix()
    readme=['# 고정200개 본문 시험 — 준비 완료, 실제 수집 미실행','',
            '큐와 초기 상태를 고정했다. actual HTTP/robots/API/HTML 다운로드/실제 뉴스 추출은 수행하지 않았다. 활용 가능한 영어 본문20,000건 이상이라는 향후 목표의 달성 여부를 이번 준비 결과로 판단하지 않는다.',
            '', '## 먼저 읽기','', '[선정 결과](pilot_selection_report.md) → [실행 계획](PILOT_EXECUTION_PLAN.md) → [품질 프로토콜](PILOT_QUALITY_PROTOCOL.md) → [검증](validation.json). 검색 설정 근거는 newscatcher_query_summary.json/evidence.json에 있다.',
            '', '## 현재 허용되는 오프라인 점검','', '프로젝트 루트에서:', '', '```powershell','$env:PYTHONDONTWRITEBYTECODE="1"',f'python -B scripts/body_pilot_200.py --run-dir {rel}','python -B -m unittest discover -s scripts -p test_body_pilot_200.py -v','```','',
            f'모의 테스트 {v["mock_tests"]["run"]}개 통과. mock_test_results.log와 mock_test_warnings.json을 확인한다. 실제 설치된 trafilatura/news-please를 합성 HTML로 호출했지만 실제 기사 성공 사례는 0건이다. 테스트 중 외부 DNS/네트워크는 차단했고 임시 mock 출력은 이 파일의 실제 article 폴더와 분리했다.',
            '', '## 별도 사용자 승인 후에만','',
            '현재 execution_approval_TEMPLATE.json의 approved=false를 유지한다. 향후 승인이 있으면 별도 승인 파일에 큐 SHA/200개/승인자·시각과 도메인별 이용 조건 근거를 기록한다. robots는 실제 실행 시 확인한다. 승인 파일이 없거나 hash가 다르면 실행이 거절된다.',
            '', '```powershell',f'python -B scripts/body_pilot_200.py --run-dir {rel} --execute --approval-file <나중에_승인된_파일.json>','```','',
            '위 실제 실행 명령은 이번에 실행하지 않았다. terms 미확인 도메인과 새 redirect 도메인은 review/차단되므로 200개가 모두 요청/성공한다고 보장하지 않는다. serial one-pass이고 due 전 오류는 보류된다. 재개는 같은 명령에서 terminal 결과를 건너뛰며 입력 큐는 바꾸지 않는다.',
            '', '## 결과 확인','',
            'pilot_state.csv의 pending/running/done/review/error/policy_blocked, events.jsonl, request_activity.json, 결과 JSON의 HTTP/HTML/추출/언어/키워드/품질·중복 항목을 구분해 집계한다. attempt는 논리 시도이며 robots/redirect 실제 응답 활동과 다를 수 있다. html/은 원 HTML, texts/는 추출기별 전체 텍스트, results/는 검토 정보, robots/는 정책 응답이다. 자동 통과를 최종 정답/20,000건 목표 달성으로 세지 않는다.',
            '', 'manifest.json은 준비 시점 파일 해시다. 실제 실행 후 state/결과/로그가 변하는 것은 명시된 mutable 산출물 변경이며 준비 manifest를 고쳐 과거를 덮어쓰지 않는다. pilot_lock.json은 큐/config/runner를 고정한다. 해당 코드/설정이 바뀌면 조용히 hash를 갱신하지 말고 새 검토·준비 버전을 만든다.',
            '', '기존 125,948개 큐, 원본 두 CSV, 기존 노트북·보고서·51건 시험은 읽기 전용으로 유지했다. Git commit/push와 패키지 다운로드를 하지 않았다.','']
    (out/'README.md').write_text('\n'.join(readme),encoding='utf-8')

if __name__=='__main__':main()
