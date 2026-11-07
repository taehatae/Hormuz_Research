"""Bounded PUBLIC POLICY review only. Never invokes article runner or extraction.

Network exists only in fetch mode; exact policy plan/redirect guard denies news
URLs. Inputs and crawler code are read-only. Raw policy HTML is never saved.
"""
from pathlib import Path
from urllib.parse import urlsplit,urljoin,urldefrag
from html.parser import HTMLParser
from datetime import datetime,timezone,timedelta
import sys,json,csv,time,re,collections,hashlib,socket,threading,queue as queues,argparse
import body_pilot_200 as p
ROOT=p.ROOT;RUN=ROOT/'hormuz_2026_data/runs/body_pilot_200_20261107_01'
KST=timezone(timedelta(hours=9))
def now():return datetime.now(KST).isoformat()
def append(name,data):
    with (RUN/name).open('a',encoding='utf-8') as f:
        f.write(json.dumps(data,ensure_ascii=False)+'\n');f.flush()
def readlines(name):
    path=RUN/name
    if not path.exists():return []
    with path.open(encoding='utf-8') as f:return [json.loads(x) for x in f if x.strip()]
POLICY_RE=re.compile(r'(?<![a-z0-9])(?:terms|copyright|licens(?:e|ing)|legal|crawling[-_]?policy|robots[-_]?policy|content[-_]?usage)(?![a-z0-9])',re.I)
POLICY_PATH_RE=re.compile(r'^/(?:(?:about|about-us|info|en|legal|tools|policies|help|about-pbs)/)*(?:terms(?:[-_](?:and|of|conditions|condition|service|services|use))*|website-terms|copyright(?:[-_](?:policy|notice|licensing|information))*|licensing|license|legal|crawling-policy|content-usage)(?:\.(?:html?|php|cms))?/?(?:digital-subscription/?)?$',re.I)
PATHS={
 'www.politico.com':'/terms-of-service','news.cgtn.com':'/terms-of-use.html',
 'www.aaj.tv':'/terms-and-conditions','timesofindia.indiatimes.com':'/termsandcondition.cms',
 'www.pbs.org':'/about/about-pbs/terms-of-use/','www.knau.org':'/terms-of-use',
 'www.reutersconnect.com':'/terms-and-conditions','www.thenationalnews.com':'/terms-and-conditions/',
 'www.cnbc.com':'/terms/','www.news18.com':'/terms-of-use/','www.rawstory.com':'/terms-of-service/',
 'www.scmp.com':'/terms-conditions','www.foxnews.com':'/terms-of-use','www.breitbart.com':'/terms-of-use/',
 'www.marketbeat.com':'/terms-of-service/','cointelegraph.com':'/terms-and-conditions',
 'www.thegatewaypundit.com':'/terms-of-service/','www.bbc.co.uk':'/usingthebbc/terms',
 'www.nytimes.com':'https://help.nytimes.com/hc/en-us/articles/115014893428-Terms-of-Service',
 'www.fxstreet.com':'/info/terms-conditions','www.dw.com':'/en/terms-of-use/a-31709906',
 'www.forbes.com':'/terms/','www.timesofisrael.com':'/terms-of-use/','flipboard.com':'/terms',
 'www.newsmax.com':'/termsconditions/','thehill.com':'/terms-of-service/',
 'www.cnbctv18.com':'/terms-of-use/','www.moneycontrol.com':'/terms-of-use/',
 'www.jpost.com':'/terms-of-service','english.alarabiya.net':'/tools/terms-and-conditions',
 'www.iranintl.com':'/en/terms-and-conditions','www.semafor.com':'/terms-of-service',
 'www.mprnews.org':'/terms','www.kedm.org':'/terms-of-use','earth.org':'/terms-and-conditions/',
 'www.khaleejtimes.com':'/terms-and-conditions','www.middleeasteye.net':'/terms-and-conditions',
 'www.the-star.co.ke':'/terms-and-conditions','www.trtworld.com':'/terms-of-use',
 'www.tradersunion.com':'/terms-and-conditions/','tradersunion.com':'/terms-and-conditions/',
 'www.newsbytesapp.com':'/terms-and-conditions','www.thedailystar.net':'/terms-of-use',
 'www.businessupturn.com':'/terms-and-conditions/','news-pravda.com':'/terms-of-use/',
 'www.world-today-news.com':'/terms-and-conditions/','www.onenewspage.com':'/terms.php',
 'www.newsy-today.com':'/terms-and-conditions/','www.aol.co.uk':'/terms-of-service/',
 'www.bitget.com':'/legal/terms-of-use','phemex.com':'/terms-of-use',
 'www.barrons.com':'/terms-of-use','www.ii.co.uk':'/legal/terms-of-service',
 'finimize.com':'/terms','eulerpool.com':'/en/terms','longbridge.com':'/en/terms-and-conditions'
}
class Text(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.skip=0;self.bits=[];self.title=[];self.in_title=False;self.links=[];self.heading=[];self.in_h1=False
    def handle_starttag(self,tag,attrs):
        if tag in {'script','style','noscript','svg'}:self.skip+=1
        if tag=='title':self.in_title=True
        if tag=='h1':self.in_h1=True
        if tag=='a':
            a=dict(attrs)
            if a.get('href'):self.links.append(a['href'])
    def handle_endtag(self,tag):
        if tag in {'script','style','noscript','svg'}:self.skip=max(0,self.skip-1)
        if tag=='title':self.in_title=False
        if tag=='h1':self.in_h1=False
    def handle_data(self,data):
        if self.skip:return
        if self.in_title:self.title.append(data)
        elif self.in_h1:self.heading.append(data);self.bits.append(data)
        else:self.bits.append(data)
def inspect_policy(body,url):
    t=Text();t.feed(body.decode('utf-8',errors='replace'));text=re.sub(r'\s+',' ',' '.join(t.bits)).strip()
    title=' '.join(t.title);heading=' '.join(t.heading)
    confirmed=bool(POLICY_RE.search(title+' '+heading)) and not re.search(r'not found|404|access denied|just a moment|enable javascript',title+' '+heading,re.I)
    automation=re.compile(r'robot|scrap(?:e|er|ing)|crawl(?:er|ing)?|spider|automated|text and data mining|machine[- ]readable',re.I)
    m=automation.search(text)
    excerpt=''
    if m:
        # At most 20 verbatim words per policy page, centered on relevant terms.
        words=list(re.finditer(r'\S+',text));idx=next((i for i,w in enumerate(words) if w.end()>m.start()),0)
        start=max(0,idx-7);excerpt=' '.join(w.group() for w in words[start:start+20])
    return dict(policy_document_confirmed=confirmed,document_title=title[:200],h1=heading[:200],
        automation_excerpt_20_words=excerpt,automation_signal=bool(m),
        copyright_signal=bool(re.search(r'all rights reserved|copyright|reproduc|licen[cs]',text,re.I)),
        summary='자동화 관련 문구 발견; 발췌만으로 허가 확정하지 않음' if m else '명시적 자동 수집 허가 근거 확인 못함',
        policy_links=list(dict.fromkeys(urldefrag(urljoin(url,h))[0] for h in t.links if POLICY_RE.search(urlsplit(h).path)))[:12])
def initial():
    path=RUN/'policy_review_before.json'
    if path.exists():return
    rows,c=p.load_locked_pilot(RUN);states=p.StateStore(RUN,rows).load()
    assert len(rows)==200 and all(r['status']=='pending' and r['attempts']=='0' for r in states)
    files=[f for f in RUN.iterdir() if f.is_file() and not f.name.startswith('POLICY_REVIEW_')]
    files+=[ROOT/'scripts/body_pilot_200.py',ROOT/'hormuz_2026_data/01_newscatcher_raw.csv',ROOT/'hormuz_2026_data/05_extraction_candidates_v2.csv',ROOT/'hormuz_2026_data/runs/candidate_selection_20261107_01/candidate_queue.csv']
    p.atomic_json(path,dict(created_at=now(),files={f.relative_to(ROOT).as_posix():dict(sha256=p.sha(f),bytes=f.stat().st_size,mtime_ns=f.stat().st_mtime_ns) for f in files},selected200=[dict(article_id=r['article_id'],request_url=r['request_url'],distributed_order=r['distributed_order'],pilot_order=r['pilot_order']) for r in rows]))
    counts=collections.Counter(urlsplit(r['request_url']).hostname for r in rows)
    plan=[]
    for host,n in counts.items():
        value=PATHS.get(host,'/terms-and-conditions/')
        term=value if value.startswith('https://') else f'https://{host}{value}'
        plan.append(dict(hostname=host,URL_count=n,robots_url=f'https://{host}/robots.txt',terms_candidate=term,lookup_basis='known/local referenced policy path or bounded candidate; not confirmed until fetched'))
    p.atomic_json(RUN/'policy_request_plan.json',dict(domains=plan,domain_limit=4,total_HTTP_limit=688,concurrency=1,min_interval_sec=2,retries=0,created_at=now()))
    print(json.dumps(dict(baseline_saved=True,domains=len(counts),URLs=len(rows))))

class Collector:
    def __init__(self,plan,rows):
        self.plan=plan;self.rows=rows;self.article_keys={(urlsplit(r['request_url']).hostname,urlsplit(r['request_url']).path,urlsplit(r['request_url']).query) for r in rows}
        self.hosts={d['hostname'] for d in plan['domains']}|{urlsplit(d['terms_candidate']).hostname for d in plan['domains']}
        self.hosts|={h[4:] if h.startswith('www.') else 'www.'+h for h in list(self.hosts)}
        self.config=dict(p.jread(RUN/'pilot_config.json'),connect_timeout_sec=5,read_timeout_sec=7,response_deadline_sec=12)
        self.transport=p.NativeTransport(self.config);self.guard=p.URLGuard(resolver=self.resolve)
        self.last={};self.counts=collections.Counter();self.stopped=set();self.total=0
        self.seen=set()
        for a in readlines('policy_network_requests.jsonl'):
            if a.get('phase')=='started' and a['kind']!='dns':
                self.counts[a['hostname']]+=1;self.total+=1;self.seen.add(a['url'])
                age=max(0,(datetime.now(KST)-datetime.fromisoformat(a['queried_at'])).total_seconds());self.last[a['hostname']]=time.monotonic()-age
            if a.get('phase')=='error' or (a.get('phase')=='response' and (a.get('http_status') in {401,403,429} or (a.get('http_status') or 0)>=500)):self.stopped.add(a['hostname'])
    def resolve(self,host,port):
        result=queues.Queue(maxsize=1)
        def work():
            try:result.put((True,p.URLGuard.resolve(host,port)))
            except Exception as e:result.put((False,e))
        append('policy_network_requests.jsonl',dict(kind='dns',phase='started',hostname=host,port=port,queried_at=now()))
        threading.Thread(target=work,daemon=True).start()
        try:ok,value=result.get(timeout=5)
        except queues.Empty:raise TimeoutError('policy_DNS_deadline')
        if not ok:raise value
        append('policy_network_requests.jsonl',dict(kind='dns',phase='response',hostname=host,addresses=value,queried_at=now()))
        return sorted(value,key=lambda x:':' in x)
    def permitted(self,url,kind,owner=None):
        info=self.guard.check(url,resolve=False);u=urlsplit(url)
        if u.scheme!='https':raise p.PolicyBlocked('policy_lookup_requires_HTTPS')
        if (u.hostname,u.path,u.query) in self.article_keys:raise p.PolicyBlocked('selected_article_URL_never_requested')
        if u.hostname not in self.hosts:raise p.ReviewNeeded('policy_redirect_unreviewed_host')
        if owner:
            d=next(x for x in self.plan['domains'] if x['hostname']==owner)
            allowed={owner,urlsplit(d['terms_candidate']).hostname}
            allowed|={h[4:] if h.startswith('www.') else 'www.'+h for h in list(allowed)}
            if u.hostname not in allowed:raise p.ReviewNeeded('policy_redirect_other_publisher_host')
        if kind=='robots' and (u.path!='/robots.txt' or u.query):raise p.ReviewNeeded('robots_redirect_not_exact_robots_endpoint')
        if kind=='policy' and u.query and url not in {x['terms_candidate'] for x in self.plan['domains']}:raise p.ReviewNeeded('unplanned_policy_query_not_requested')
        if kind=='policy' and url not in {x['terms_candidate'] for x in self.plan['domains']} and not POLICY_PATH_RE.fullmatch(u.path):raise p.ReviewNeeded('not_a_policy_only_path_article_links_denied')
        return info
    def get(self,url,kind,owner,interval=2.):
        origin=urlsplit(url)
        for hop in range(3):
            self.permitted(url,kind,owner);host=urlsplit(url).hostname
            if url in self.seen:raise p.ReviewNeeded('policy_URL_already_requested_no_refetch')
            if host in self.stopped:raise p.ReviewNeeded('host_previous_access_or_connection_failure_no_repeat')
            if self.counts[host]>=4 or self.total>=688:raise p.ReviewNeeded('policy_request_budget_exhausted')
            wait=self.last.get(host,0)+max(2.,interval)-time.monotonic()
            if wait>10:raise p.ReviewNeeded('robots_delay_requires_later_review_no_long_wait')
            if wait>0:time.sleep(wait)
            try:target=self.guard.check(url)
            except Exception as e:
                self.stopped.add(host);append('policy_network_requests.jsonl',dict(kind='dns',phase='error',hostname=host,url=url,error_type=type(e).__name__,detail=str(e)[:180],queried_at=now()))
                raise
            self.last[host]=time.monotonic();self.counts[host]+=1;self.total+=1;rid=self.total;self.seen.add(url)
            append('policy_network_requests.jsonl',dict(kind=kind,phase='started',request_id=rid,owner_hostname=owner,hostname=host,url=url,redirect_hop=hop,public_IP=target['ip'],queried_at=now()))
            try:r=self.transport.get_validated(url,262144 if kind=='robots' else 524288,target)
            except Exception as e:
                self.stopped.add(host);append('policy_network_requests.jsonl',dict(kind=kind,phase='error',request_id=rid,owner_hostname=owner,hostname=host,url=url,error_type=type(e).__name__,detail=str(e)[:160],http_status=getattr(e,'http_status',None),queried_at=now()))
                raise
            append('policy_network_requests.jsonl',dict(kind=kind,phase='response',request_id=rid,owner_hostname=owner,hostname=host,url=url,http_status=r.status,headers=r.headers,bytes=len(r.body),sha256=hashlib.sha256(r.body).hexdigest(),queried_at=now()))
            if r.status in {401,403,429} or r.status>=500:self.stopped.add(host)
            if r.status in {301,302,303,307,308}:
                target=urljoin(url,r.headers.get('location',''));v=urlsplit(target)
                if not r.headers.get('location') or v.scheme!='https':raise p.ReviewNeeded('policy_redirect_missing_or_downgrade')
                if kind=='robots' and (v.scheme,v.netloc)!=(origin.scheme,origin.netloc):raise p.ReviewNeeded('robots_cross_origin_redirect_review')
                self.permitted(target,kind,owner);url=target;continue
            if r.headers.get('content-encoding','identity').lower() not in {'','identity'}:raise p.ReviewNeeded('policy_encoding_not_identity')
            return r
        raise p.ReviewNeeded('policy_redirect_limit')
    def review(self,d):
        host=d['hostname'];record=dict(d,queried_at=now(),robots_status='review',terms_status='review',policy_status='review',terms_document='',copyright_url='',robots_paths={},uncertainties=[])
        interval=2.
        try:
            r=self.get(d['robots_url'],'robots',host);record['robots_HTTP_status']=r.status;record['robots_final_url']=r.url
            if r.status in {404,410}:record['robots_status']='no_directives';record['robots_paths']={row['article_id']:'allowed_no_directives' for row in self.rows if row['domain']==host}
            elif r.status==200:
                rp=p.RobotsRules(r.body,self.config['robots_agent']);interval=max(2.,rp.interval)
                record['robots_status']='parsed';record['robots_interval_sec']=interval
                record['robots_selected_rules']=[list(x) for x in rp.rules]
                record['robots_paths']={row['article_id']:('allowed' if rp.allowed(row['request_url']) else 'disallowed') for row in self.rows if row['domain']==host}
                dest=RUN/'policy_review_robots'/f'{host}.txt';dest.parent.mkdir(exist_ok=True);dest.write_bytes(r.body)
                record['robots_saved_path']=str(dest.relative_to(RUN));record['robots_sha256']=p.sha(dest)
            else:record['uncertainties'].append('robots_HTTP_'+str(r.status))
        except Exception as e:record['uncertainties'].append('robots_'+type(e).__name__+':'+str(e)[:180])
        try:
            r=self.get(d['terms_candidate'],'policy',host,interval);record['terms_HTTP_status']=r.status;record['terms_final_url']=r.url
            inspected=inspect_policy(r.body,r.url)
            if r.status==200 and inspected['policy_document_confirmed']:
                record.update(inspected);record['terms_document']=r.url
            else:
                # One bounded alternative found on the policy endpoint response;
                # never request a homepage or discovered article.
                candidates=[u for u in inspected['policy_links'] if u!=r.url and urlsplit(u).hostname in self.hosts and re.search(r'terms|copyright|licens',urlsplit(u).path,re.I)]
                if candidates and r.status not in {401,403,429} and r.status<500:
                    alt=candidates[0];self.permitted(alt,'policy');r2=self.get(alt,'policy',host,interval);inspected=inspect_policy(r2.body,r2.url)
                    record['terms_alternative_HTTP_status']=r2.status
                    if r2.status==200 and inspected['policy_document_confirmed']:record.update(inspected);record['terms_document']=r2.url
                if not record['terms_document']:record['uncertainties'].append('official_terms_document_not_confirmed_in_bounded_lookup')
        except Exception as e:record['uncertainties'].append('terms_'+type(e).__name__+':'+str(e)[:180])
        if not record['terms_document']:record['uncertainties'].append('automated_article_collection_permission_unknown')
        else:record['uncertainties'].append('policy_text_requires_contextual_permission_review')
        record['completed_at']=now()
        append('policy_review_raw.jsonl',record)
        append('policy_evidence.jsonl',dict(evidence_id=host,hostname=host,robots_URL=d['robots_url'],terms_candidate_URL=d['terms_candidate'],confirmed_terms_URL=record['terms_document'],queried_at=record['queried_at'],completed_at=record['completed_at'],robots_status=record['robots_status'],robots_HTTP_status=record.get('robots_HTTP_status'),terms_HTTP_status=record.get('terms_HTTP_status'),policy_title=record.get('document_title',''),short_excerpt=record.get('automation_excerpt_20_words',''),summary=record.get('summary','공개 정책 문서 확인 불가; 허가 추정 안 함'),uncertainties=record['uncertainties']))
        print(json.dumps(dict(host=host,robots=record['robots_status'],terms_confirmed=bool(record['terms_document']),HTTP_started=self.total),ensure_ascii=False),flush=True)

def fetch(batch):
    initial();rows,c=p.load_locked_pilot(RUN);plan=p.jread(RUN/'policy_request_plan.json');done={x['hostname'] for x in readlines('policy_review_raw.jsonl')};collector=Collector(plan,rows)
    todo=[d for d in plan['domains'] if d['hostname'] not in done][:batch];start=time.monotonic()
    for d in todo:
        if time.monotonic()-start>480:break
        collector.review(d)
    print(json.dumps(dict(domains_complete=len(readlines('policy_review_raw.jsonl')),total_domains=len(plan['domains']),HTTP_started=collector.total,batch_elapsed_sec=round(time.monotonic()-start,2))),flush=True)
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('mode',choices=['init','fetch']);a.add_argument('--batch',type=int,default=20);args=a.parse_args()
    if args.mode=='init':initial()
    else:fetch(min(max(args.batch,1),40))
