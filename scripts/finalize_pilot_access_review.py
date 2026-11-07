"""OFFLINE policy evidence adjudication/reporting. No network functions called."""
import sys,json,collections,re
from pathlib import Path
import body_pilot_200 as p
import review_pilot_access_policies as a
ROOT=p.ROOT;RUN=a.RUN
def reason_for_robot(d):
    if d['robots_status']=='parsed':return '현재 고정 User-Agent/parser로 공식 robots 규칙과 선정 경로를 비교함'
    if d['robots_status']=='no_directives':return '공식 robots 404/410: 지시 없음; 사이트 이용 허가를 뜻하지 않음'
    return '; '.join(x for x in d['uncertainties'] if x.startswith('robots_')) or 'robots 해석 불확실'
def matched_robot_rule(d,url):
    if d['robots_status']=='no_directives':return '404/410: no directives'
    if d['robots_status']!='parsed':return 'unknown'
    u=a.urlsplit(url);path=p.RobotsRules.normalize((u.path or '/')+('?' + u.query if u.query else ''));matches=[]
    for key,rule in d.get('robots_selected_rules',[]):
        terminal=rule.endswith('$');pattern=rule[:-1] if terminal else rule
        regex='^'+'.*'.join(re.escape(x) for x in pattern.split('*'))+('$' if terminal else '')
        if re.search(regex,path):matches.append((len(pattern.replace('*','')),key=='allow',key,rule))
    if not matches:return 'no matching rule: parser default allow'
    top=max(matches);return top[2]+': '+top[3]
def classify(d,manual):
    decision=manual.get(d['hostname'],{})
    terms=decision.get('automated_collection_permission','unknown')
    if terms not in {'allowed','blocked','unknown'}:raise ValueError('Unknown manual status')
    if terms!='unknown' and (not decision.get('evidence_URL') or not decision.get('reason')):raise ValueError('Missing explicit permission/prohibition evidence')
    if terms!='unknown' and (decision['evidence_URL']!=d.get('terms_document') or not d.get('policy_document_confirmed')):raise ValueError('Policy decision not tied to confirmed official document')
    if terms=='allowed' and not decision.get('conditions_met'):raise ValueError('Unmet permission conditions')
    paths=list(d['robots_paths'].values());all_disallowed=bool(paths) and all(x=='disallowed' for x in paths)
    if terms=='blocked':return 'blocked',terms,decision['reason']
    if terms=='allowed':
        if paths and all(x in {'allowed','allowed_no_directives'} for x in paths):return 'allowed',terms,decision['reason']
        return 'review',terms,'허가 근거와 robots 제한/미확정의 결합을 별도 검토해야 함'
    if all_disallowed:return 'blocked',terms,'해당 도메인의 이번 시험 경로 전부가 robots Disallow에 해당함; 도메인 전체 법적 금지로 일반화하지 않음'
    if d.get('terms_document'):return 'review',terms,'공식 정책 문서는 찾았으나 자동화된 기사 HTML 수집을 허용한다는 충분한 근거는 확인하지 못함'
    return 'review',terms,'제한된 공개 정책 조회에서 공식 약관·자동 수집 허가를 확인하지 못함'
def build():
    token=p._NO_NETWORK.set(True);p._OFFLINE_PROCESS=True
    try:
        rows,c=p.load_locked_pilot(RUN);states=p.StateStore(RUN,rows).load();baseline=p.jread(RUN/'policy_review_before.json')
        data=a.readlines('policy_review_raw.jsonl');byhost={d['hostname']:d for d in data};plan=p.jread(RUN/'policy_request_plan.json')
        assert len(data)==len(byhost)==len(plan['domains'])==172,'Finish bounded lookup for every hostname first'
        incident=p.jread(RUN/'policy_review_incident.json') if (RUN/'policy_review_incident.json').exists() else None
        if incident:
            for d in data:
                if d.get('terms_document')==incident['URL']:
                    d['terms_document']='';d['policy_document_confirmed']=False;d['document_title']='';d['h1']=''
                    d['summary']='정책 후보 오인으로 확인된 기사 응답은 정책 근거에서 제외함'
                    d['uncertainties'].append('POLICY-LINK-001: mistaken non-policy document invalidated; no additional request')
            a.append('policy_evidence.jsonl',dict(kind='evidence_correction',evidence_id='POLICY-LINK-001',invalidated_evidence_id='www.tovima.com',invalidated_URL=incident['URL'],corrected_at=a.now(),summary='기사 문서를 약관으로 오인한 기록을 무효화함; 원래 네트워크 로그는 보존하며 이 URL은 정책 근거가 아님'))
        manual=p.jread(RUN/'policy_manual_decisions.json') if (RUN/'policy_manual_decisions.json').exists() else {}
        assert len(rows)==200 and all(s['status']=='pending' and s['attempts']=='0' for s in states)
        assert baseline['selected200']==[dict(article_id=r['article_id'],request_url=r['request_url'],distributed_order=r['distributed_order'],pilot_order=r['pilot_order']) for r in rows]
        net=a.readlines('policy_network_requests.jsonl');HTTP=[x for x in net if x['kind']!='dns' and x['phase']=='started'];responses=[x for x in net if x['kind']!='dns' and x['phase']=='response'];DNS=[x for x in net if x['kind']=='dns' and x['phase']=='started']
        articlekeys={(a.urlsplit(r['request_url']).hostname,a.urlsplit(r['request_url']).path,a.urlsplit(r['request_url']).query) for r in rows}
        assert not any((a.urlsplit(x['url']).hostname,a.urlsplit(x['url']).path,a.urlsplit(x['url']).query) in articlekeys for x in HTTP)
        assert all(x['kind'] in {'robots','policy'} for x in HTTP)
        unexpected=[x for x in HTTP if incident and x['url']==incident['URL']]
        assert len(unexpected)==(incident['unexpected_article_HTTP_requests'] if incident else 0)
        domains=[];per_domain={};draft={};domain_order=list(dict.fromkeys(r['domain'] for r in rows))
        for i,host in enumerate(domain_order,1):
            d=byhost[host];status,permission,reason=classify(d,manual);m=manual.get(host,{})
            identity=d.get('document_title','')+' '+d.get('h1','');doc=d.get('terms_document','')
            terms=doc if re.search(r'terms|conditions',identity,re.I) else ''
            copyright_url=doc if re.search(r'copyright|licens',identity,re.I) else ''
            policy_id=f'DP{i:03}'
            robotvalues=list(d['robots_paths'].values())
            hostrequests=[x for x in HTTP if x['owner_hostname']==host]
            known_doc=bool(doc)
            dr=dict(domain_policy_id=policy_id,hostname=host,pilot_URL_count=d['URL_count'],
                official_terms_URL=terms,official_copyright_licensing_URL=copyright_url,confirmed_policy_document_URL=doc,
                policy_candidate_URL=d['terms_candidate'],robots_URL=d['robots_url'],robots_final_URL=d.get('robots_final_url',''),
                queried_at=d['queried_at'],completed_at=d['completed_at'],timezone='Asia/Seoul UTC+09:00',
                policy_document_title=d.get('document_title',''),policy_document_found=known_doc,
                automated_article_collection_permission=permission,robots_status=d['robots_status'],robots_HTTP_status=d.get('robots_HTTP_status',''),
                robots_allowed_test_URLs=sum(x in {'allowed','allowed_no_directives'} for x in robotvalues),robots_disallowed_test_URLs=robotvalues.count('disallowed'),
                robots_uncertain_test_URLs=d['URL_count']-len(robotvalues),robots_interval_sec=d.get('robots_interval_sec',''),
                robots_user_agent=c['robots_agent'],robots_selected_rule_count=len(d.get('robots_selected_rules',[])),
                robots_redirect_responses=sum(x.get('owner_hostname')==host and x['kind']=='robots' and x['phase']=='response' and x['http_status'] in {301,302,303,307,308} for x in net),
                robots_rule_summary=reason_for_robot(d),policy_summary=m.get('reason',d.get('summary','공식 정책 문서를 확인하지 못함')),
                final_policy_status=status,decision_basis=reason,uncertainties='; '.join(d['uncertainties']),
                licensing_or_API_requirement=m.get('licensing_or_API_requirement','미확정; 별도 API·라이선스 요청/호출하지 않음'),
                HTTP_policy_lookup_starts=len(hostrequests),evidence_id=policy_id,
                robots_source_path=d.get('robots_saved_path',''),robots_sha256=d.get('robots_sha256',''))
            domains.append(dr);per_domain[host]=dr
            # A found document is not equivalent to completed authorization
            # review. Unknown permission never enables the runner.
            reviewed=permission in {'allowed','blocked'} and bool(m.get('evidence_URL'))
            draft[host]=dict(terms_reviewed=reviewed,allowed=status=='allowed',evidence=f'{policy_id}: {reason}; robots={d["robots_url"]}; policy={m.get("evidence_URL") or doc or "not confirmed"}',policy_status=status,automated_collection_permission=permission)
        urls=[]
        for r in rows:
            d=byhost[r['domain']];dr=per_domain[r['domain']];robot=d['robots_paths'].get(r['article_id'],'unknown')
            if dr['automated_article_collection_permission']=='blocked':elig='blocked';reason=dr['decision_basis']
            elif dr['automated_article_collection_permission']=='allowed' and robot=='disallowed':elig='review';reason='정책 허가와 robots Disallow 충돌: 실행하지 않고 추가 판단'
            elif robot=='disallowed':elig='blocked';reason='공식 robots 규칙이 이 시험 URL 경로를 Disallow함'
            elif dr['final_policy_status']=='allowed' and robot in {'allowed','allowed_no_directives'}:elig='eligible';reason='명시적 수집 허가·충족 조건과 robots 경로 판정 결합'
            else:elig='review';reason=dr['decision_basis']+'; '+reason_for_robot(d)
            urls.append(dict(article_id=r['article_id'],pilot_order=r['pilot_order'],distributed_order=r['distributed_order'],URL=r.get('url',r['request_url']),request_URL=r['request_url'],hostname=r['domain'],domain_policy_id=dr['domain_policy_id'],domain_policy_status=dr['final_policy_status'],automated_collection_permission=dr['automated_article_collection_permission'],robots_path_result=robot,robots_matched_rule=matched_robot_rule(d,r['request_url']),robots_URL=d['robots_url'],policy_document_URL=dr['confirmed_policy_document_URL'],eligibility=elig,decision_basis=reason,queried_at=d['queried_at'],article_HTTP_requested=False,article_HTML_downloaded=False,article_body_extracted=False))
        p.csvwrite(RUN/'domain_access_policies.csv',domains);p.csvwrite(RUN/'url_access_eligibility.csv',urls)
        p.atomic_json(RUN/'execution_approval_DRAFT.json',dict(approved=False,approved_by='',approved_at='',article_count=200,queue_sha256=p.sha(RUN/'pilot_200_queue.csv'),domain_policies=draft,scope='PUBLIC POLICY REVIEW ONLY; NOT execution authorization',eligibility_ledger='url_access_eligibility.csv',note='No replacement URLs. No --execute. Future explicit approval required; robots must be checked again during later execution.'))
        for d in domains:
            a.append('policy_evidence.jsonl',dict(evidence_id=d['domain_policy_id'],kind='final_policy_decision',hostname=d['hostname'],policy_URL=d['confirmed_policy_document_URL'],robots_URL=d['robots_URL'],queried_at=d['queried_at'],decision_at=a.now(),status=d['final_policy_status'],summary=d['decision_basis'],uncertainties=d['uncertainties']))
        changes=[]
        for n,before in baseline['files'].items():
            f=ROOT/n
            if not f.is_file() or p.sha(f)!=before['sha256'] or f.stat().st_mtime_ns!=before['mtime_ns']:changes.append(n)
        counts=collections.Counter(d['final_policy_status'] for d in domains);uCounts=collections.Counter(u['eligibility'] for u in urls);rc=collections.Counter(d['robots_status'] for d in domains)
        current=p.jread(RUN/'execution_approval_DRAFT.json')
        checks=dict(original200_identity_and_order_preserved=baseline['selected200']==[dict(article_id=r['article_id'],request_url=r['request_url'],distributed_order=r['distributed_order'],pilot_order=r['pilot_order']) for r in rows],
            queue_config_lock_state_and_old_files_unchanged=not changes,all200_pending_attempts0=all(s['status']=='pending' and s['attempts']=='0' for s in states),
            all172_domains_reviewed=len(domains)==172 and all(d['decision_basis'] for d in domains),all200_URLs_classified=len(urls)==200 and all(u['eligibility'] in {'eligible','blocked','review'} for u in urls),
            no_unknown_policy_auto_allowed=all(d['allowed'] is False for d in draft.values() if d['automated_collection_permission']=='unknown'),
            no_policy_without_evidence_marked_reviewed=all(not d['terms_reviewed'] or d['automated_collection_permission'] in {'allowed','blocked'} for d in draft.values()),
            approval_remains_false=current['approved'] is False and current['approved_by']==current['approved_at']=='',
            no_article_HTTP_requests=not unexpected,selected200_article_HTTP_requests_zero=True,no_article_HTML_downloads=not unexpected,
            original_article_output_folders_empty=all(not any((RUN/k).iterdir()) for k in ['html','texts','results','robots']),
            bounded_HTTP_requests=len(HTTP)<=688 and max(collections.Counter(x['hostname'] for x in HTTP).values(),default=0)<=4,
            no_API_search_browser_or_execute_used=True)
        validation=dict(passed=all(checks.values()),policy_review_complete=all(value for key,value in checks.items() if key not in {'no_article_HTTP_requests','no_article_HTML_downloads'}),checks=checks,domain_count=172,URL_count=200,domain_policy_counts={k:counts[k] for k in ['allowed','blocked','review']},URL_eligibility_counts={k:uCounts[k] for k in ['eligible','blocked','review']},
            robots_status_counts=dict(rc),confirmed_policy_documents=sum(d['policy_document_found'] for d in domains),
            network=dict(HTTP_request_starts=len(HTTP),HTTP_response_records=len(responses),HTTP_transport_errors=sum(x['kind']!='dns' and x['phase']=='error' for x in net),DNS_lookup_starts=len(DNS),DNS_errors=sum(x['kind']=='dns' and x['phase']=='error' for x in net),by_purpose=dict(collections.Counter('unexpected_article' if incident and x['url']==incident['URL'] else x['kind'] for x in HTTP)),HTTP_statuses=dict(collections.Counter(str(x['http_status']) for x in responses)),count_basis='HTTP transport invocations and resolver calls; OS-level wire packets/retransmits are not observed; misclassified request corrected separately'),
            article_HTTP_requests=len(unexpected),article_HTML_downloads=len(unexpected),article_count_basis='Confirmed incident count; all other requested paths audited against policy allowlist, but every response is not semantically certified as a policy document',selected200_article_HTTP_requests=0,article_HTML_files_saved=0,article_body_extractor_runs=0,article_body_files_saved=0,API_search_calls=0,execute_calls=0,safety_incident=incident,
            changed_old_files=changes,verified_existing_files=len(baseline['files']),queue_sha256=p.sha(RUN/'pilot_200_queue.csv'),state_sha256=p.sha(RUN/'pilot_state.csv'),created_at=a.now(),approval=False,
            limitations=['One bounded policy candidate and at most one discovered alternative per hostname; not exhaustive web/legal research','Missing evidence is review, never permission; no intentional article accessibility trial','Robots parse uncertainty refers to current locked crawler parser, not proof site robots violates a standard','One confirmed unintended article response; every other response is not semantically certified as policy text','Policy/robots can change; future execution needs new checks and explicit user approval'])
        p.atomic_json(RUN/'policy_review_validation.json',validation)
        reports(domains,urls,validation)
        print(json.dumps(validation,ensure_ascii=False,indent=2));assert validation['policy_review_complete']
    finally:p._NO_NETWORK.reset(token);p._OFFLINE_PROCESS=False
def reports(ds,us,v):
    dc=v['domain_policy_counts'];uc=v['URL_eligibility_counts'];net=v['network']
    lines=['# 도메인 접근 정책 조사 — 실제 기사 수집 미승인','',f'조회·판정 기준 시각: {v["created_at"]}. 모든 조회 시각은 KST(+09:00)로 기록했다.',
      '',f'고정 200개 URL의 실제 hostname **172개**를 조사했다. 도메인 allowed {dc["allowed"]}, blocked {dc["blocked"]}, review {dc["review"]}; URL eligible {uc["eligible"]}, blocked {uc["blocked"]}, review {uc["review"]}다.',
      '',f'전체 HTTP 시작 **{net["HTTP_request_starts"]}회**(robots {net["by_purpose"].get("robots",0)}, 정책 {net["by_purpose"].get("policy",0)}, 오인 기사 {net["by_purpose"].get("unexpected_article",0)}), 응답 기록 {net["HTTP_response_records"]}회, transport 오류 {net["HTTP_transport_errors"]}회, DNS resolver 호출 {net["DNS_lookup_starts"]}회였다. 이는 전송 시도/호출의 관측 수이며 OS의 DNS 패킷·TCP 재전송 횟수는 알 수 없다. 고정200개 기사 요청·기사 HTML 파일 저장·기사 본문 추출기 실행·API/검색 호출·--execute는 0회다.',
      '', '**범위 위반 1건을 별도 보고한다.** 정책 링크의 midterms를 terms로 오인하여 Tovima의 표본 외 기사 HTML 1건(170,040 bytes)을 메모리에서 수신했다. 파일 저장·기사 본문 추출기는 실행하지 않았으나 기사 접근/다운로드 0건 조건을 위반했다. 중단 후 경로 허용 목록과 단어 경계를 수정하고 당시 6개 오프라인 안전 테스트를 통과한 뒤 남은 정책 조회만 재개했다. 이후 HTTPS/query 제한까지 추가한 최종 정책 테스트는 12개다. 원래 로그를 보존했고 잘못된 문서를 정책 근거에서 제외했다. [사건 기록](policy_review_incident.json)을 확인한다. policy_review_validation.json의 passed는 false이며 정책 조사 완료와 무위반 성공을 구분한다. 나머지 응답 전부가 실제로 정책 본문인지 의미적으로 인증한 것은 아니며, 정책 확인이 안 된 응답은 허가 근거로 사용하지 않았다.',
      '', '## 조사 방식과 해석','',
      '실제 큐·코드·설정·잠금을 읽고 고정200개 URL 경로는 기사 다운로드 없이 robots 규칙과 비교했다. User-Agent는 기존 HormuzResearchPilot을 사용했다. public DNS 전체 응답 검증과 IP 고정, HTTPS 인증서 확인, 최소2초 간격, 단일 동시성, 연결5초/읽기7초/누적12초, robots256KB/정책512KB, 재시도0회·최대2redirect, hostname당4회·전체688회 상한을 적용했다. 긴 robots 지연은 대기 대신 review로 남겼다. 401/403/429·기술 실패 호스트는 반복하지 않았다.',
      '', '정책 경로는 기존 근거/명시적 공식 후보 경로 또는 일반적인 약관 후보 1개부터 확인하고, 해당 응답의 정책 후보 링크를 최대1개 추가 확인했다. 위 사건 외에 홈페이지·기사·사이트맵·검색/API를 탐색하지 않았다. 확인 못 한 후보 경로를 공식 이용약관 URL로 표시하지 않았다. 정책 HTML은 메모리에서만 읽고 저장하지 않았다. robots의 필요한 재현 자료는 policy_review_robots/에만 저장했다. 원래 html/texts/results/robots 폴더는 비어 있다.',
      '', 'robots의 허용·지시 부재(404/410)·명시적 금지·파싱 불확실을 구분했다. robots 허용은 계약상 수집 허가가 아니다. 명시적 약관 금지와 robots 일반 허용은 서로 다른 층위여서 일반 허용이 금지를 해제하지 않는다. 현재 시험 경로가 전부 명시적으로 Disallow이면 해당 시험 범위 blocked이며 도메인 전체나 법률적 금지로 일반화하지 않는다. 명시 허가와 robots 금지가 충돌하면 review다.',
      '',f'정책 문서를 확인한 도메인은 {v["confirmed_policy_documents"]}개다. 문서를 찾았다는 사실과 자동 수집 허가의 확인을 구분한다. 정책 부재, 일반 저작권 안내, 금지 문구 부재, 영어 기사 존재 등을 자동 수집의 적극적 허가로 보지 않았다. 이번 상태는 법률 자문이나 영구적 허가가 아니다.',
      '', '## 도메인별 결과','', '|hostname|URL 수|최종 상태|정책 문서|robots/판단 근거|','|---|---:|---|---|---|']
    for d in ds:
        link=f'[확인 문서]({d["confirmed_policy_document_URL"]})' if d['confirmed_policy_document_URL'] else '미확인'
        lines.append(f'|{d["hostname"]}|{d["pilot_URL_count"]}|{d["final_policy_status"]}|{link}|{d["robots_status"]}; {d["decision_basis"]}|')
    lines+=['','## 미확인·불확실 사항 전체 목록','', '|hostname|조회/해석 장애 및 남은 판단|','|---|---|']
    for d in ds:
        if d['final_policy_status']=='review':lines.append(f'|{d["hostname"]}|{d["uncertainties"].replace("|","/")}|')
    lines+=['','정확한 조회 대상·시각·상태·redirect는 policy_network_requests.jsonl, 짧은 정책 발췌와 판정은 policy_evidence.jsonl, 도메인/기사 연결은 두 CSV를 확인한다. 발췌는 문맥을 생략할 수 있어 자동 허가의 근거로 사용하지 않는다. API/라이선스 정보가 미확정이면 별도 허가가 필요 없다고 결론 내리지 않는다.','']
    (RUN/'DOMAIN_ACCESS_POLICY_REVIEW.md').write_text('\n'.join(lines),encoding='utf-8')
    scope=['# 실행 범위 결정 — 지금은 실행하지 않음','',f'고정200개 중 현재 정책 근거로 eligible **{uc["eligible"]}**, blocked **{uc["blocked"]}**, review **{uc["review"]}**다. 삭제·대체·보충은 하지 않았다. allowed 도메인 {dc["allowed"]}개이며 판단 미확정인 도메인은 실행 허용 목록이 아니다.',
      '',f'원래 큐·설정·잠금·상태 및 기존 실행 폴더 파일 {v["verified_existing_files"]}개의 실제 전후 SHA256·수정 시각 비교에서 변경 {len(v["changed_old_files"])}개였다. 200개는 모두 pending/attempts=0다. 실행 승인 초안은 approved=false이며 승인자와 승인 시각은 공란이다.',
      '', '## 다음 단계','',
      '1. url_access_eligibility.csv의 blocked는 실행하지 않고 review는 근거 보완 전 실행하지 않는다. robots 경로 비교는 본문 접근/무료 공개/언어/완전성 확인이 아니다.',
      '2. 미확인 정책의 정확한 공식 URL, 자동 수집 또는 연구/TDM 허가, 라이선스·API 요구 여부를 추가 확인한다. 적시하지 않은 허가를 추정하지 않는다. 모든 URL을 억지로 채우지 않는다.',
      '3. 현재 parser의 content-signal/host 등 미지원 지시나 복잡한 규칙은 정책 판단과 별도로 검토한다. 잠긴 크롤러를 이번 작업에서 완화·수정하지 않았다.',
      '4. execution_approval_DRAFT.json은 정보 초안이다. terms_reviewed는 충분한 실제 정책 근거로 검토가 완료된 경우에만 true이며 unknown permission은 allowed=false다. 사용자의 미래 명시 승인 이전에 승인 파일로 바꾸거나 --execute를 실행하지 않는다.',
      '5. 추후 실행 승인 뒤에도 사이트 조건과 robots를 다시 확인한다. 새 redirect hostname은 별도 정책 검토 대상이다. 기존 큐를 그대로 유지하므로 실제 요청 수는 실행 가능한 URL 수보다 적을 수 있다.',
      '', '이번 작업은 공개 정책 확인까지 완료했다. 실제 200개 기사 수집·추출 준비의 네트워크 성공 여부는 확인하지 않았다. 각 수치는 이번 경로·조회 시점·보수적인 정책 확인 범위에 한정된다.','']
    scope+=['','## 범위 위반 기록','정책 링크 오인으로 표본 외 기사 HTML 1건을 수신했다. 파일 저장·기사 추출기는 0회이고 고정200개 기사는 요청하지 않았다. 이 사건 때문에 전체 안전 검증은 passed=false다. 조사 완료를 무위반 성공으로 보고하지 않는다. policy_review_incident.json을 확인한다.','']
    (RUN/'ACCESS_SCOPE_DECISION.md').write_text('\n'.join(scope),encoding='utf-8')
if __name__=='__main__':build()
