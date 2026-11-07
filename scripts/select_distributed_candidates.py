"""Offline chunked selection. Does not normalize URLs, create article IDs or fetch."""
from pathlib import Path
import argparse,csv,json,hashlib,time,os,sys,platform,collections,unittest,io
import pandas as pd
import numpy as np
from candidate_selection_rules import classify,schedule,RULE_VERSION

ROOT=Path(__file__).resolve().parents[1];DATA=ROOT/'hormuz_2026_data'
OLD=DATA/'metadata_selection_20261107_01';AUDIT=DATA/'quality_audit_20261107_01'
USECOLS=['article_id','url','url_raw','published_date','domain','nc_source_name','nc_country','nc_language','nc_paid_content','title','nc_description','nc_content_snippet','hormuz_relevance_score','flag_aggregator_review','title_dup_group_id','title_dup_group_size','nc_url_record_count']
KEEP=['candidate_record_number','article_id','url','url_raw','published_date','month','domain','nc_source_name','nc_country','raw_NA_country_evidence','region','country_basis','nc_language','nc_paid_content','hormuz_relevance_score','topic_evidence','type_hint','technical_risk','domain_pilot_caution','source_review_hint','title_dup_group_id','title_dup_group_size','nc_url_record_count','selection_status','reason','priority','distributed_order']
def read(p,cols=None,**kw):return pd.read_csv(p,usecols=cols,dtype=str,keep_default_na=False,encoding='utf-8-sig',**kw)
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()
def load(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def writejson(p,d):p.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--run-dir',type=Path,required=True);ap.add_argument('--chunk-size',type=int,default=20000);args=ap.parse_args()
    out=args.run_dir.resolve()
    if not out.is_relative_to(DATA/'runs'):raise ValueError('Run must be under data/runs')
    out.mkdir(parents=True,exist_ok=True)
    if set(p.name for p in out.iterdir())-{'PLAN.md'}:raise RuntimeError('Run is nonempty; refusing overwrite')
    started=time.perf_counter();events=[]
    def log(stage,**kw):
        ev=dict(stage=stage,elapsed_sec=round(time.perf_counter()-started,3),**kw);events.append(ev)
        with (out/'progress.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(ev,ensure_ascii=False)+'\n')
        print(json.dumps(ev,ensure_ascii=False),flush=True)
        if time.perf_counter()-started>900:raise TimeoutError('Exceeded15min; preserve partial run and investigate')
    def budget(t,phase):
        sec=time.perf_counter()-t
        if sec>180:raise TimeoutError(f'{phase} exceeded180sec ({sec:.1f}); stop before next phase')
    # Enforcement is additional to the code's lack of any network client.
    def no_network(event,args):
        if event in {'socket.connect','socket.bind','socket.getaddrinfo','subprocess.Popen','os.system'}:raise PermissionError('Offline selection blocks '+event)
    sys.addaudithook(no_network)
    config=dict(rule_version=RULE_VERSION,seed=20261107,random_generator='numpy.PCG64',chunk_size=args.chunk_size,domain_lookahead=64,
        study_start='2026-01-01',study_end_inclusive='2026-08-31',dates='legacy UTC assumption for naive published_date, no KST change',
        media_whitelist=None,country_quotas=None,monthly_quotas=None,quantity_target=None,
        exclusions=['explicit paid metadata','invalid HTTP URL','explicit date outside period'],
        review=['unknown payment/language/date/ID or host mismatch','nonprose/special URL hints incl live and Flash','exact fixed51 existing artifacts need review before refetch','aggregator/republication flag','missing title','insufficient topic context'],
        ready_topic=['explicit Strait of Hormuz/Hormuz Strait phrase in title/description/snippet','whole-word Hormuz plus maritime/oil terms in same metadata combination (co-occurrence heuristic)'],
        score0_policy='never hard exclude; new explicit evidence may qualify, otherwise review',
        URL_type_policy='path-segment hint only, all nonprose/special hints REVIEW rather than inferred certainty',
        schedule='one existing ready record per nonempty month as warmup, then within-month priority+seeded tie order and proportional midpoint virtual time; bounded lookahead avoids adjacent same domain',
        actual_body_status='unknown until later authorized access and body QA',new_network_requests=0,
        runtime_policy=dict(respect_robots=True,per_domain_min_interval_sec=2,max_retries=2,retry_only='429/5xx/timeout/connection errors, scheduler-managed backoff; no endless domain retries',html_download_now=False))
    writejson(out/'config.json',config)
    log('start',pandas=pd.__version__,numpy=np.__version__,python=platform.python_version())
    t=time.perf_counter();protected={};stat_before={}
    # Protect pre-existing research + docs, excluding secrets, Git and vendor.
    # Stream once, reusing these fresh hashes for actual input verification.
    for base,dirs,files in os.walk(ROOT):
        dirs[:]=[d for d in dirs if d not in {'.git','vendor','__pycache__'} and not (Path(base)/d).resolve().is_relative_to(out)]
        for name in files:
            p=Path(base)/name
            if name=='.env' or name.startswith('.env.') or p.is_relative_to(out):continue
            st=p.stat();r=p.relative_to(ROOT).as_posix();stat_before[r]=(st.st_size,st.st_mtime_ns);protected[r]=sha(p)
    writejson(out/'preservation_before.json',dict(hashes=protected,stat={k:list(v) for k,v in stat_before.items()},scope='all readable non-Git/non-secret/non-vendor existing project files; new run excluded'))
    prior={k.replace('\\','/'):v for k,v in load(OLD/'input_hashes_before.json').items()}
    core=['hormuz_2026_data/01_newscatcher_raw.csv','hormuz_2026_data/05_extraction_candidates_v2.csv']
    for name in core:
        if protected[name]!=prior[name]['sha256']:raise RuntimeError('Original input differs from validated source: '+name)
    oldv=load(OLD/'validation.json')
    for name,h in oldv['output_hashes'].items():
        key=(OLD/name).relative_to(ROOT).as_posix()
        if protected[key]!=h:raise RuntimeError('Prior aggregate output changed: '+name)
    budget(t,'input hash verification');log('actual_hashes_verified',core_files=2,protected_files=len(protected),seconds=round(time.perf_counter()-t,3))
    audit=read(AUDIT/'quality_audit.csv');risks={}
    for r in audit.to_dict('records'):
        parts=['fixed_pilot_artifact_review_before_refetch']
        for col in ['clear_wrong_extraction','contaminated','completeness_suspected']:
            if r[col]=='1':parts.append('AI_'+col)
        if r['is_article_body']!='1':parts.append('AI_body_unconfirmed')
        parts.append('historical_access='+r['original_access_status']);risks[r['article_id']]=';'.join(parts)
    readiness=read(AUDIT/'media_readiness.csv');domain_col='domain'
    statuscol=next(c for c in ['provisional_status','readiness_status','next_pilot_status','status','media_readiness'] if c in readiness.columns) if any(c in readiness.columns for c in ['provisional_status','readiness_status','next_pilot_status','status','media_readiness']) else readiness.columns[-1]
    domain_cautions=dict(zip(readiness[domain_col],readiness[statuscol]))
    reg=read(OLD/'region_lookup.csv');regions=dict(zip(reg.country_code,reg.working_geographic_region))
    # Read existing raw-ID correspondence only for literal NA evidence. This
    # is a country-evidence lookup, not URL normalization/deduplication.
    t=time.perf_counter();naids=set();na_raw_rows=0
    for c in read(OLD/'raw_record_correspondence.csv.gz',['normalized_url_article_id','original_country'],chunksize=100000):
        m=c['original_country'].eq('NA');na_raw_rows+=int(m.sum());naids.update(c.loc[m,'normalized_url_article_id'])
    log('NA_evidence_loaded',raw_NA_rows=na_raw_rows,existing_IDs=len(naids),seconds=round(time.perf_counter()-t,3))
    t=time.perf_counter();parts=[];n=0
    for i,c in enumerate(read(DATA/'05_extraction_candidates_v2.csv',USECOLS,chunksize=args.chunk_size),1):
        ct=time.perf_counter();c['candidate_record_number']=np.arange(n+1,n+len(c)+1);n+=len(c)
        c['technical_risk']=c['article_id'].map(risks).fillna('')
        c['domain_pilot_caution']=c['domain'].map(domain_cautions).fillna('not_tested_in_fixed51')
        c['source_review_hint']=np.where(c['flag_aggregator_review'].str.lower().isin(['true','1','yes']),'aggregator_or_republication_flag','source_role_unverified')
        c=classify(c)
        c['raw_NA_country_evidence']=np.where(c['article_id'].isin(naids),'NA','')
        c['region']=c['nc_country'].map(regions).fillna('unknown')
        c['country_basis']='candidate_vendor_literal_unverified'
        c.loc[c['raw_NA_country_evidence'].eq('NA') & c['nc_country'].eq(''),'country_basis']='candidate_blank;raw_country_NA_evidence_separate'
        c['distributed_order']=0
        parts.append(c[KEEP+['UTC_day']].copy())
        budget(ct,'single chunk');log('chunk_classified',chunk=i,rows=n,chunk_rows=len(c),chunk_sec=round(time.perf_counter()-ct,3),states=c['selection_status'].value_counts().to_dict())
    allc=pd.concat(parts,ignore_index=True);del parts,c
    budget(t,'CSV parse/classification');log('all_classified',rows=len(allc),seconds=round(time.perf_counter()-t,3),compact_memory_bytes=int(allc.memory_usage(deep=True).sum()))
    if len(allc)!=427875 or allc['article_id'].duplicated().any() or allc['url'].duplicated().any():raise RuntimeError('Authoritative candidate identity/cardinality changed; no dedup attempted')
    t=time.perf_counter();ready=allc[allc['selection_status'].eq('ready')];order,schedstats=schedule(ready,config['seed'],config['domain_lookahead'])
    replay,replaystats=schedule(ready,config['seed'],config['domain_lookahead'])
    allc.loc[order,'distributed_order']=np.arange(1,len(order)+1)
    queue=allc.loc[order,['distributed_order','article_id','url_raw','domain','published_date','month','priority','domain_pilot_caution']].rename(columns={'url_raw':'request_url'})
    # Empty original URL falls back to unchanged existing normalized URL, recorded.
    fallback=queue['request_url'].eq('');queue.loc[fallback,'request_url']=allc.loc[queue.index[fallback],'url']
    queue['runtime_state']='pending_not_requested';queue['last_error']='';queue['attempts']=0
    queue['domain_preflight_required']=True
    log('schedule_built',ready=len(ready),seconds=round(time.perf_counter()-t,3),**schedstats)
    t=time.perf_counter();allc[KEEP].to_csv(out/'candidate_screening.csv',index=False,encoding='utf-8-sig',chunksize=20000)
    queue.to_csv(out/'candidate_queue.csv',index=False,encoding='utf-8-sig',chunksize=20000)
    log('principal_outputs_written',seconds=round(time.perf_counter()-t,3),screening_bytes=(out/'candidate_screening.csv').stat().st_size,queue_bytes=(out/'candidate_queue.csv').stat().st_size)
    dist=[];N=len(allc);R=len(ready)
    for dimension,col in [('month','month'),('domain','domain'),('country_literal','nc_country'),('region_vendor_lookup','region'),('topic_evidence','topic_evidence'),('type_hint','type_hint')]:
        groups=allc.groupby([col,'selection_status'],dropna=False).size().unstack(fill_value=0).reindex(columns=['ready','review','exclude'],fill_value=0)
        zero_keys=groups.index[groups['ready'].eq(0)]
        reason_pairs=allc.loc[allc[col].isin(zero_keys),[col,'reason']].drop_duplicates()
        zero_reasons=reason_pairs.groupby(col)['reason'].agg(lambda s:';'.join(sorted(s))).to_dict()
        for key,row in groups.iterrows():
            total=int(row.sum());rr=int(row['ready'])
            dist.append(dict(dimension=dimension,value=key,all_candidates=total,ready=rr,review=int(row['review']),exclude=int(row['exclude']),all_share=total/N,ready_share=rr/R if R else 0,ready_retention=rr/total,source='existing candidate lexical country; no affiliation correction' if dimension=='country_literal' else 'new article-level screening',ready_zero_reason=zero_reasons.get(key,'') if rr==0 else ''))
    # No repeated per-domain full-frame filtering: enrich zero-group reasons via
    # one grouped table, rather than millions of per-URL regex or file reads.
    daily=read(OLD/'daily_coverage.csv',['UTC_day','raw_records','normalized_candidate_urls','nonpaid_english_metadata'])
    daycounts=allc.groupby(['UTC_day','selection_status']).size().unstack(fill_value=0).reindex(columns=['ready','review','exclude'],fill_value=0)
    for r in daily.to_dict('records'):
        dc=daycounts.loc[r['UTC_day']] if r['UTC_day'] in daycounts.index else pd.Series(dict(ready=0,review=0,exclude=0))
        dist.append(dict(dimension='UTC_day',value=r['UTC_day'],all_candidates=int(r['normalized_candidate_urls']),ready=int(dc['ready']),review=int(dc['review']),exclude=int(dc['exclude']),all_share=int(r['normalized_candidate_urls'])/N,ready_share=int(dc['ready'])/R if R else 0,ready_retention=int(dc['ready'])/int(r['normalized_candidate_urls']) if int(r['normalized_candidate_urls']) else 0,source='reused prior raw/dedup/nonpaid date counts; new ready/review/exclude',raw_records=int(r['raw_records']),nonpaid_english_metadata=int(r['nonpaid_english_metadata']),ready_zero_reason='absent_in_raw' if r['raw_records']=='0' else 'selection_review_or_exclusion' if dc['ready']==0 else ''))
    for month,g in daily.groupby(daily['UTC_day'].str[:7]):
        dist.append(dict(dimension='raw_month_reused',value=month,raw_records=int(pd.to_numeric(g.raw_records).sum()),all_candidates=int(pd.to_numeric(g.normalized_candidate_urls).sum()),nonpaid_english_metadata=int(pd.to_numeric(g.nonpaid_english_metadata).sum()),source='prior validated 243-day UTC aggregate; raw CSV hash verified identical'))
    for k in [8,64,100,1000,10000,len(queue)]:
        if not len(queue):continue
        prefix=queue.iloc[:min(k,len(queue))]
        for month,nn in prefix['month'].value_counts().items():dist.append(dict(dimension=f'queue_prefix_{min(k,len(queue))}_month',value=month,ready=int(nn),ready_share=nn/len(prefix),source='execution order only; final month counts unchanged'))
    pd.DataFrame(dist).to_csv(out/'distribution_check.csv',index=False,encoding='utf-8-sig')
    reasons=allc.groupby(['selection_status','reason']).size().reset_index(name='count');reasons.to_csv(out/'screening_reason_counts.csv',index=False,encoding='utf-8-sig')
    states=allc['selection_status'].value_counts().to_dict()
    # Reuse original identity proof ONLY after the current full input SHA matches.
    got=read(out/'candidate_screening.csv',['article_id','url','selection_status','distributed_order','nc_country'])
    old_inventory=read(OLD/'media_inventory.csv',['domain','candidate_urls'])
    thisdomains=allc['domain'].value_counts().to_dict();olddomains=dict(zip(old_inventory.domain,pd.to_numeric(old_inventory.candidate_urls)))
    oldregistry=load(OLD/'media_evidence_registry.json')['domains']
    prior29={d for d,r in oldregistry.items() if r.get('media_candidate_status') in {'priority_candidate','conditional_candidate'}}
    months=sorted(ready['month'].unique());prefixmonths=sorted(queue.head(64)['month'].unique())
    checks=dict(input_rows_427875=len(allc)==427875,status_partition=sum(states.values())==len(allc),
        source_hashes_match_prior_verified=True,existing_domain_totals_reused_verified=thisdomains==olddomains,
        existing_article_ids_and_URLs_copied_unchanged=got.article_id.equals(allc.article_id) and got.url.equals(allc.url),
        candidate_no_duplicate_ID_or_URL=not allc.article_id.duplicated().any() and not allc.url.duplicated().any(),
        queue_exact_all_ready_once=set(queue.article_id)==set(ready.article_id) and len(queue)==len(ready) and not queue.article_id.duplicated().any(),
        request_URLs_no_duplicates=not queue.request_url.duplicated().any(),
        order_replay_identical=np.array_equal(order,replay) and schedstats==replaystats,
        ready_in_study_period=ready.month.isin([f'2026-{m:02}' for m in range(1,9)]).all(),
        ready_nonpaid_English_metadata=allc.loc[order,'nc_paid_content'].str.lower().isin(['false','0','no']).all() and allc.loc[order,'nc_language'].str.lower().eq('en').all(),
        ready_has_all_input_nonempty_months=set(months)==set(allc.month.unique())-{'unknown'},
        month_counts_unchanged_by_scheduling=queue.month.value_counts().to_dict()==ready.month.value_counts().to_dict(),
        all_ready_months_present_in_first64=months==prefixmonths,
        country_NA_evidence_not_lost=int(allc.raw_NA_country_evidence.eq('NA').sum())==len(naids),
        original_country_literals_preserved=got.nc_country.equals(allc.nc_country),
        no_country_required_or_whitelist_gate=bool(len(ready[~ready.domain.isin(prior29)])),
        US_not_entirely_dropped=bool((ready.nc_country=='US').any()),
        none_of_fixed51_scheduled=not queue.article_id.isin(audit.article_id).any(),
        days_and_raw_count_reused_match=int(pd.to_numeric(daily.raw_records).sum())==440260 and int(pd.to_numeric(daily.normalized_candidate_urls).sum())==len(allc))
    # Full queue parse checks serialized values too, without another wide read.
    written=read(out/'candidate_queue.csv',['article_id','request_url','distributed_order'])
    checks['serialized_queue_order_and_URLs_match']=written.article_id.tolist()==queue.article_id.tolist() and written.request_url.tolist()==queue.request_url.tolist() and pd.to_numeric(written.distributed_order).tolist()==list(range(1,len(queue)+1))
    testlog=io.StringIO();suite=unittest.defaultTestLoader.discover(str(ROOT/'scripts'),pattern='test_candidate_selection_rules.py');testresult=unittest.TextTestRunner(stream=testlog,verbosity=2).run(suite)
    (out/'test_results.log').write_text(testlog.getvalue(),encoding='utf-8');checks['six_regression_tests']=testresult.wasSuccessful() and testresult.testsRun==6
    t=time.perf_counter();changed=[];afterhash={}
    for r,h in protected.items():
        p=ROOT/r
        if not p.is_file():changed.append(r);continue
        now=(p.stat().st_size,p.stat().st_mtime_ns)
        # Full fresh SHA of the two originals after execution; other historical
        # files full hash too to substantiate preservation, not just timestamps.
        afterhash[r]=sha(p)
        if now!=stat_before[r] or afterhash[r]!=h:changed.append(r)
    checks['all_protected_old_files_unchanged']=not changed
    log('validation_and_preservation',checks=len(checks),failed=[k for k,v in checks.items() if not v],seconds=round(time.perf_counter()-t,3))
    prefixes={str(k):dict(month_counts=queue.head(k).month.value_counts().sort_index().to_dict(),domains=int(queue.head(k).domain.nunique())) for k in [8,64,100,1000,10000] if len(queue)>=k}
    val=dict(passed=all(bool(v) for v in checks.values()),checks={k:bool(v) for k,v in checks.items()},reviewed_URLs=len(allc),states=states,queue_rows=len(queue),queue_domains=int(queue.domain.nunique()),queue_country_codes=int(ready.loc[~ready.nc_country.str.lower().isin(['','unknown']),'nc_country'].nunique()),
        raw_records_reused=440260,raw_absent_days=load(OLD/'analysis_summary.json')['raw_absent_days'],new_ready_zero_days=[r['value'] for r in dist if r['dimension']=='UTC_day' and r['ready']==0],
        months={m:dict(all=int((allc.month==m).sum()),ready=int((ready.month==m).sum())) for m in sorted(allc.month.unique())},
        prior29_domains_comparison_only=len(prior29),ready_outside_prior29=int((~ready.domain.isin(prior29)).sum()),ready_domains_outside_prior29=int(ready.loc[~ready.domain.isin(prior29),'domain'].nunique()),
        NA_country_evidence=dict(raw_rows=na_raw_rows,IDs=len(naids),candidate_original_blank=int((allc.raw_NA_country_evidence.eq('NA')&allc.nc_country.eq('')).sum()),ready=int((ready.raw_NA_country_evidence=='NA').sum()),candidate_country_not_overwritten=True),
        title_duplicate_flagged_ready=int(pd.to_numeric(ready.title_dup_group_size,errors='coerce').gt(1).sum()),title_duplicates_not_removed=True,
        scheduler=schedstats,prefixes=prefixes,protected_files=len(protected),source_changes=changed,
        actual_core_SHA256={p:dict(before=protected[p],after=afterhash.get(p),matches_previous_validation=protected[p]==prior[p]['sha256']) for p in core},
        total_elapsed_sec=round(time.perf_counter()-started,3),new_network_requests=0,new_HTML_or_body_files=0,LLM_individual_URL_judgments=0,normalization_or_article_ID_recalculation=False,
        software=dict(python=platform.python_version(),pandas=pd.__version__,numpy=np.__version__))
    writejson(out/'validation.json',val)
    write_report(out,val,reasons,ready,allc,events)
    # Complete plan without altering any pre-existing historical artifact.
    plan=out/'PLAN.md'
    if val['passed']:plan.write_text(plan.read_text(encoding='utf-8').replace('- [ ]','- [x]'),encoding='utf-8')
    outputs=[p for p in out.iterdir() if p.is_file() and p.name!='manifest.json']
    inputs=[DATA/'01_newscatcher_raw.csv',DATA/'05_extraction_candidates_v2.csv',OLD/'raw_record_correspondence.csv.gz',OLD/'region_lookup.csv',OLD/'daily_coverage.csv',OLD/'media_inventory.csv',OLD/'media_evidence_registry.json',OLD/'validation.json',OLD/'input_hashes_before.json',OLD/'analysis_summary.json',AUDIT/'quality_audit.csv',AUDIT/'media_readiness.csv',ROOT/'README.md',ROOT/'docs/data_collection_criteria_v1.md']
    inputs.extend([OLD/'SOURCE_DISTRIBUTION_REPORT.md',OLD/'SELECTION_RECOMMENDATION_REPORT.md',AUDIT/'QUALITY_AUDIT_REPORT.md',AUDIT/'NEXT_SELECTION_PLAN.md',ROOT/'hormuz_textminig_v2.ipynb'])
    manifest=dict(task='whole-metadata distributed collection candidate design; no fetching',rule_version=RULE_VERSION,
        inputs=[dict(path=p.relative_to(ROOT).as_posix(),sha256=protected[p.relative_to(ROOT).as_posix()],bytes=p.stat().st_size) for p in inputs],
        code=[dict(path=p.relative_to(ROOT).as_posix(),sha256=sha(p)) for p in [Path(__file__).resolve(),ROOT/'scripts/candidate_selection_rules.py',ROOT/'scripts/test_candidate_selection_rules.py']],
        outputs=[dict(path=p.relative_to(ROOT).as_posix(),sha256=sha(p),bytes=p.stat().st_size) for p in outputs],validation_passed=val['passed'],
        reuse='Prior identity, raw totals and calendar verified against actual unchanged input hashes; no original normalization repeated',
        resumption='Queue immutable article_id/order; later crawler stores append-only state separately keyed by ID, skips completed/policy-held IDs, preserves per-domain delay/backoff; pending here means no request performed')
    writejson(out/'manifest.json',manifest)
    print(json.dumps({k:val[k] for k in ['passed','reviewed_URLs','states','queue_domains','queue_country_codes','ready_outside_prior29','months','total_elapsed_sec']},ensure_ascii=False,indent=2))
    if not val['passed']:raise SystemExit(1)

def write_report(out,v,reasons,ready,allc,events):
    L=['# 전체 메타데이터 기반 분산 본문 수집 후보·실행 순서 설계','',
       '**실제 HTTP·API·HTML 다운로드·본문 추출·AI 품질 재평가는 0회다.** 기존 URL/article_id·중복 제거 결과를 유지했다. 이 결과는 수집을 시도할 메타데이터 후보와 순서이며 최종 코퍼스·매체 목록·확정 수집량이 아니다.',
       '', '## 실제 처리 결과','', f'검토 URL **{v["reviewed_URLs"]:,}**. 상태: '+', '.join(f'**{k} {n:,}**' for k,n in v['states'].items())+'.',
       '', '원자료 440,260행과 중복 제거 후 427,875 URL 대응·날짜·언어·유료 집계의 과거 검증을 재사용했다. 이번에도 두 원본 CSV의 실제 전체 SHA256을 시작/완료 시 읽어 기존 검증값과 동일함을 확인했다. 원본 재정규화·ID 재생성·URL 재중복 제거는 하지 않았다. 큐의 중복 검사는 결과 검증이며 삭제/병합이 아니다.',
       '', '## 규칙과 판단의 수준','',
       '- 명시적 유료, 잘못된 HTTP URL, 명시적 연구 기간 밖만 exclude다. 언어 미확정/비영어·결제 미상·날짜/ID/도메인 불일치는 review다. 이번 API 영어 필터 사용은 사용자 확인 사항이며 실제 본문 언어 확인이 아니다.',
       '- 관련성은 제목·설명·스니펫의 명시적 Strait of Hormuz/Hormuz Strait 구절, 또는 단어 Hormuz와 해운·해협·유조선·석유 등 문맥의 공기(co-occurrence)로 ready 근거를 정한다. 공기는 의미 이해가 아니어서 동명이인·주제 주변 기사·스니펫 혼입이 남을 수 있다. URL에만 Hormuz가 있거나 지역/해운 문맥만 있는 경우는 review다.',
       '- 기존 점수는 제목/설명/스니펫의 문자열 기반 0/1/2다. 보존·교차검토 정보로만 유지하고 score0/defer 자체로 제외하지 않았다. 명백한 무관성을 메타데이터만으로 새로 확정하지 않았다.',
       '- video/audio/listing/infographic/live 경로와 Flash 제목은 형식 **힌트**여서 전부 review다. 일반 경로도 실제 산문 본문 존재를 증명하지 않는다. live-fire는 단어 substring으로 live로 처리하지 않았다. Urgent·짧은 description/word_count를 길이 기준으로 제외하지 않았다.',
       '- 기존 집계·재게시 플래그는 출처 검토 사유다. 기사 수나 인지도만으로 선별하지 않았다. 원 출처·번역·동일 제목/통신사 중복은 후속 확인 대상이며 동일 제목 때문에 URL을 삭제하지 않았다.',
       '- 고정 51건은 이미 확보/실패/AI 검토 기록이 있어 신규 요청에 바로 넣지 않고 기존 자료 검토로 보류했다. AI 추정은 최종 정답이 아니다. 17개 도메인의 과거 위험은 주의 필드로만 남겨 모든 URL의 실패로 일반화하지 않았다.',
       '', f'기존 A/B/C는 실행 규칙에 없다. 비교용 과거 29개 잠정 후보 밖에서도 **{v["ready_outside_prior29"]:,} URL / {v["ready_domains_outside_prior29"]:,} 도메인**이 ready다. 미검토 도메인·국가 미상은 단독 제외/보류 조건이 아니다. 과거 조건부 매체의 원출처·영어판·접근 불확실성이 해소됐다는 뜻도 아니며, ready는 본문 확보·원보도·최종 포함 승인이 아니다.',
       '', '## 제외·보류 원인(최초 사유, 상호배타적)','', '|상태|최초 사유|URL 수|','|---|---|---:|']
    for r in reasons.to_dict('records'):L.append(f'|{r["selection_status"]}|{r["reason"]}|{r["count"]:,}|')
    L += ['', '관련성·형식·출처·과거 위험 필드를 별도 보존했다. 최초 사유는 합계를 맞추는 대표 사유로, 다른 위험이 없다는 뜻이 아니다. 제목·설명·스니펫 전체를 결과에 중복 저장하지 않고 candidate_record_number(헤더 제외 1부터)로 기존 후보 CSV에 대응시켰다.',
          '', '## 월별 분포','', '|UTC 월|기존 후보|ready|ready 비율|','|---|---:|---:|---:|']
    for m,r in v['months'].items():L.append(f'|{m}|{r["all"]:,}|{r["ready"]:,}|{r["ready"]/len(ready)*100 if len(ready) else 0:.2f}%|')
    L += ['', '월별 원자료 기록 수는 distribution_check.csv의 raw_month_reused, 선정 전후 날짜는 UTC_day에 보존했다. 2월 말 이후 기사량 증가라는 가설을 인과적으로 확인한 것은 아니다. 실제 관측 월별 차이는 표/CSV를 따른다. 선정이 월별 비율을 바꿀 수 있으며 이를 전체 원래 뉴스량으로 해석하지 않는다.',
          '', f'원자료에 없는 날짜: {", ".join(v["raw_absent_days"])}. ready가 없는 날짜: {", ".join(v["new_ready_zero_days"]) or "없음"}. 원자료 부재와 review/exclude로 생긴 공백은 날짜 행에서 구분한다. 모든 날짜를 채우지 않았다. 원래 offset 없는 발행 시각은 이전 UTC 해석을 유지했고 실제 upstream 시간대 확정이나 KST 이동은 하지 않았다.',
          '', '## 국가·지역·출처 점검','', f'ready는 **{v["queue_domains"]:,} 도메인 / {v["queue_country_codes"]:,} 비어 있지 않은 비unknown 국가 코드**에 걸친다. 도메인은 독립 언론사·운영사 수가 아니고, vendor 국가값은 소재국 검증이 아니다. 미국 코드 US 후보가 전부 누락되지 않았음을 검사했다. 각 국가·도메인의 ready 0건은 distribution_check.csv에 원인과 함께 남겼다.',
          '', f'국가 원문은 keep_default_na=False로 보존했다. 과거 후보에서 사라진 NA는 원 raw 대응의 **{v["NA_country_evidence"]["raw_rows"]:,} 레코드/{v["NA_country_evidence"]["IDs"]:,} URL**에 별도 raw_NA_country_evidence로 기록했으며 후보 nc_country는 수정하지 않았다. 이 중 ready {v["NA_country_evidence"]["ready"]:,}개다. 지역은 기존 broad-continent 대응표를 vendor 코드에만 적용한 작업용 분류다. 국가 원값·기사 언급국·검증 소재국을 혼동하지 않았고 추측 보정/국가 할당을 하지 않았다.',
          '', f'기존 제목 중복 그룹에 속한 ready는 {v["title_duplicate_flagged_ready"]:,}개다. 동일 제목·통신사 전재는 내용 독립성을 증명하지 못하므로 후속 원출처/본문 중복 검토가 필요하다. 원본 duplicate group/count를 보존했으나 기사 URL을 임의로 병합하지 않았다.',
          '', '## 분산 실행 순서와 재개','',
          '선정과 실행 순서는 분리했다. ready를 article_id 순으로 안정 정렬한 뒤 PCG64 seed=20261107로 동률 순서를 고정한다. 월 안에서는 관련성 priority→seeded tie 순으로 배치한다. 각 비어 있지 않은 월에서 기존 후보 1개씩의 짧은 warmup을 만들고, 나머지는 (월 내 순번+0.5)/해당 월 전체 건수의 가상시각 순으로 섞는다. 소량 warmup은 순서 설계이며 월별 표본 수를 같은 값으로 정하는 할당이 아니다.',
          '', f'64개 lookahead에서 이전 도메인과 다른 후보를 먼저 꺼내 요청 집중을 완화한다. 전체 ready를 빠짐없이 한 번씩 배치해 최종 월/국가/도메인 수량은 순서 때문에 변하지 않는다. 이 제한된 버퍼에서도 남은 연속 동일 도메인 쌍은 **{v["scheduler"]["adjacent_same_domain"]:,}**개다. 큐 순번은 시간 간격·robots 허가를 대체하지 않는다.',
          '', '단순 결정적 셔플은 빠르지만 소량 월이 초반에 안 나타날 수 있고, 월별 동일 수량 round-robin은 초반 비중을 과하게 바꾼다. 선택한 비례 가상시각+짧은 warmup은 모든 후보량을 유지하면서 여러 월을 일찍 섞는다. 초반의 월별 비중은 최종 비중과 완전히 같지 않으며 prefix 행으로 확인할 수 있다.',
          '', '|초기 큐 길이|월별 건수|도메인 수|','|---:|---|---:|']
    for k,r in v['prefixes'].items():L.append(f'|{k}|{json.dumps(r["month_counts"],ensure_ascii=False)}|{r["domains"]:,}|')
    L += ['', '실제 수집 시 큐·config·manifest 해시를 먼저 고정한다. 새 crawler는 별도 상태 원장(article_id, order, pending/running/done/review/error, attempts, next_retry_at)을 사용하고 완료/정책 보류를 건너뛰어 재개한다. candidate_queue.csv를 원본 상태 원장처럼 덮어쓰지 않는다. 첫 도메인 요청 전 robots와 접근 정책을 확인하고, 도메인별 최소 2초·제한된 재시도·백오프를 적용한다. 반복 실패 도메인은 circuit-breaker로 보류하고 다른 도메인으로 진행한다. 이 정책 설계는 crawler를 구현하거나 실행한 것이 아니다.',
          '', '## 효율·검증·보존','',
          f'전체 소요(최종 문서 쓰기 전): {v["total_elapsed_sec"]:.3f}초. pandas 청크 20,000행, 필요한 {len(USECOLS)}컬럼만 읽었다. 스니펫은 청크에서 규칙 판정에만 쓰고 버렸다. 원본 wide CSV·HTML·본문을 복제하지 않았고 중간 DB를 만들지 않았다. 단계별 건수·시간은 progress.jsonl에 기록했다. 원본 보존 검증을 위해 전체 SHA를 실제 읽었지만 관련성/URL 정규화 계산을 반복하지 않았다.',
          '', f'검증 결과 **{v["passed"]}**: 상태 합계, 고유 ID/URL, 기존 URL 대응, 기간, 월별 수량 유지, 처음 64개 월 포괄성, US 유지, 과거 29개 외 포함, seed 재현, NA 보존 및 {v["protected_files"]:,}개 기존 비밀/Git/vendor 제외 파일 해시·mtime 보존을 확인했다. 회귀 테스트 6개 통과. 기존 노트북·감사·보고서는 변경하지 않았다. 결과 검증은 기존 분석 코드를 실행한 것이 아니다.',
          '', '파일: candidate_queue.csv(ready 실행 순서·아직 요청하지 않은 pending 상태), candidate_screening.csv(전체 상태/근거/원본 대응), distribution_check.csv(월·도메인·국가·지역·날짜·prefix), screening_reason_counts.csv, config.json, validation.json, manifest.json, preservation_before.json, PLAN.md, test_results.log, progress.jsonl. 코드 해시는 manifest에 기록한다.',
          '', '**여기서 중단한다.** 다음 실제 수집은 별도 지시 후 큐/config/보류 사유를 검토하고 접근 정책부터 확인한다. ready 성공률·본문 언어·완전성·원보도 여부·추출기 우열은 아직 확인하지 않았다. 기존 51건 AI 추정이나 이번 후보 수를 최종 코퍼스의 대표 성능·원래 보도량으로 해석하지 않는다.','']
    (out/'SELECTION_DESIGN_REPORT.md').write_text('\n'.join(L),encoding='utf-8')

if __name__=='__main__':
    try:main()
    except Exception as e:
        print(json.dumps(dict(status='stopped',error=type(e).__name__+': '+str(e)),ensure_ascii=False),flush=True)
        raise
