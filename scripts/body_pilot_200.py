"""Fixed-200 pilot runner. Default is offline dry-run; live requires later approval.

No project notebook/pilot is imported. Redirects stay inside the active article
chain; discovered links are never scheduled. Real transport pins a verified
public IP while preserving TLS SNI/certificate validation for the original host.
"""
from pathlib import Path
from dataclasses import dataclass
from urllib.parse import urlsplit,urljoin,parse_qsl,quote
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
import argparse,csv,json,hashlib,ipaddress,socket,ssl,http.client,time,os,re,sys
import unicodedata,contextvars,collections,importlib.metadata,threading,tempfile,math
import concurrent.futures,queue

ROOT=Path(__file__).resolve().parents[1]
VENDOR=ROOT/'hormuz_2026_data/body_collection_pilot_20261102_01/vendor'
TERMINAL={'done','review','policy_blocked'}
_NO_NETWORK=contextvars.ContextVar('pilot_extract_offline',default=False)
_EXTRACTING=contextvars.ContextVar('pilot_extraction_no_writes',default=False)
_OFFLINE_PROCESS=False
def audit_network(event,args):
    if (_OFFLINE_PROCESS or _NO_NETWORK.get()) and (event.startswith('socket.') or event in {'subprocess.Popen','os.system'}):
        raise PermissionError('No network/subprocess allowed during dry-run/tests/extraction')
    if _EXTRACTING.get() and event=='open':
        mode=args[1];flags=args[2]
        if (isinstance(mode,str) and any(c in mode for c in 'wax+')) or (isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):
            raise PermissionError('No file writes from extractor or language detector')
sys.addaudithook(audit_network)

class PolicyBlocked(Exception):pass
class ReviewNeeded(Exception):pass
class RetryLater(Exception):
    def __init__(self,reason,when):super().__init__(reason);self.when=when
@dataclass
class Response:
    url:str
    status:int
    headers:dict
    body:bytes

class RobotsRules:
    """Conservative subset of REP; unknown/malformed instructions require review.

    Most specific agent groups, longest matching path, Allow on equal length.
    Unknown extensions are not silently ignored in this pilot.
    """
    def __init__(self,body,agent):
        try:text=body.decode('utf-8-sig',errors='strict')
        except UnicodeError as e:raise ReviewNeeded('robots_invalid_UTF8') from e
        if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f<>]',text):raise ReviewNeeded('robots_HTML_or_control_characters')
        groups=[];agents=[];rules=[];interval=0.;has_directive=False
        def flush():
            nonlocal agents,rules,interval,has_directive
            if agents:groups.append((agents,rules,interval))
            agents=[];rules=[];interval=0.;has_directive=False
        for raw in text.splitlines():
            line=raw.split('#',1)[0].strip()
            if not line:continue
            if ':' not in line:raise ReviewNeeded('robots_malformed_line')
            key,value=line.split(':',1);key=key.strip().lower();value=value.strip()
            if key=='user-agent':
                if not re.fullmatch(r'\*|[A-Za-z0-9_.-]+',value):raise ReviewNeeded('robots_invalid_user_agent')
                if has_directive:flush()
                agents.append(value.lower());continue
            if key=='sitemap':
                if urlsplit(value).scheme not in {'http','https'}:raise ReviewNeeded('robots_invalid_sitemap')
                continue
            if not agents:raise ReviewNeeded('robots_directive_without_agent')
            has_directive=True
            if key in {'allow','disallow'}:
                if not value:continue
                if not value.startswith('/') or re.search(r'\s',value) or ('$' in value[:-1]):raise ReviewNeeded('robots_invalid_path_rule')
                rules.append((key,self.normalize(value)))
            elif key=='crawl-delay':
                if not re.fullmatch(r'\d+(?:\.\d+)?',value):raise ReviewNeeded('robots_invalid_crawl_delay')
                delay=float(value)
                if not math.isfinite(delay):raise ReviewNeeded('robots_invalid_crawl_delay')
                interval=max(interval,delay)
            elif key=='request-rate':
                m=re.fullmatch(r'([1-9]\d*)/([1-9]\d*)',value)
                if not m:raise ReviewNeeded('robots_invalid_request_rate')
                interval=max(interval,int(m[2])/int(m[1]))
            else:raise ReviewNeeded('robots_unsupported_directive:'+key)
        flush();agent=agent.lower()
        scored=[(max((0 if a=='*' else len(a) for a in aa if a=='*' or a in agent),default=-1),rr,delay) for aa,rr,delay in groups]
        best=max((s for s,r,d in scored),default=-1)
        self.rules=[rule for s,rr,d in scored if s==best and s>=0 for rule in rr]
        self.interval=max((d for s,rr,d in scored if s==best and s>=0),default=0.)
    @staticmethod
    def normalize(value):
        if re.search(r'%(?![0-9a-fA-F]{2})',value):raise ReviewNeeded('robots_invalid_percent_encoding')
        value=quote(value,safe="/%*?$&=:+,;@!~'()-._")
        return re.sub(r'%([0-9a-fA-F]{2})',lambda m:chr(int(m[1],16)) if chr(int(m[1],16)) in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~' else '%'+m[1].upper(),value)
    def allowed(self,url):
        u=urlsplit(url);path=self.normalize((u.path or '/')+('?' + u.query if u.query else ''));matches=[]
        for key,rule in self.rules:
            terminal=rule.endswith('$');pattern=rule[:-1] if terminal else rule
            regex='^'+'.*'.join(re.escape(x) for x in pattern.split('*'))+('$' if terminal else '')
            if re.search(regex,path):matches.append((len(pattern.replace('*','')),key=='allow'))
        return max(matches,default=(0,True))[1]

def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(4194304),b''):h.update(b)
    return h.hexdigest()
def jread(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def atomic_json(p,obj):atomic_bytes(p,json.dumps(obj,ensure_ascii=False,indent=2).encode('utf-8'))
def atomic_bytes(p,data):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_name(p.name+'.tmp');tmp.write_bytes(data);os.replace(tmp,p)
def csvread(p):
    with Path(p).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def csvwrite(p,rows,fields=None):
    p=Path(p);tmp=p.with_name(p.name+'.tmp');p.parent.mkdir(parents=True,exist_ok=True)
    with tmp.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields or list(rows[0]));w.writeheader();w.writerows(rows)
    os.replace(tmp,p)
def iso(t):return datetime.fromtimestamp(t,timezone.utc).isoformat()
def timestamp(s):
    if not s:return 0.
    try:return float(s)
    except ValueError:return datetime.fromisoformat(s).timestamp()

def default_config():
    return dict(schema_version='body_pilot_200_v1',exact_URL_count=200,network_enabled_by_default=False,
        user_agent='HormuzResearchPilot/0.2 (academic research; fixed 200-URL feasibility pilot)',robots_agent='HormuzResearchPilot',
        concurrency=1,per_domain_min_interval_sec=2.,connect_timeout_sec=10,read_timeout_sec=30,response_deadline_sec=60,
        max_retries=2,backoff_base_sec=2.,max_retry_after_sec=3600,circuit_failure_threshold=3,circuit_cooldown_sec=900,
        max_redirects=5,max_html_bytes=8000000,max_robots_bytes=512000,primary='trafilatura_default',compare_precision=True,
        include_comments=False,language_seed=0,short_body_words_review=80,language_probability_review=.80,
        near_duplicate_simhash_distance=3,near_duplicate_min_words=80,
        actual_useful_English_body_minimum_future_goal=20000,current_research_hypothesis=None,
        access_status_is_not_free_body_verification=True,never_choose_longest=True,fixture_results_not_real_articles=True)

class URLGuard:
    def __init__(self,resolver=None):self.resolver=resolver or self.resolve
    @staticmethod
    def resolve(host,port):return list(dict.fromkeys(r[4][0] for r in socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)))
    def check(self,url,resolve=True):
        try:
            if not isinstance(url,str) or re.search(r'[\x00-\x20\x7f\\]',url):raise PolicyBlocked('URL_control_space_or_backslash')
            u=urlsplit(url);host=(u.hostname or '').rstrip('.').encode('idna').decode('ascii').lower()
            if u.scheme not in {'http','https'} or not host or u.username is not None or u.password is not None:raise PolicyBlocked('URL_scheme_host_or_credentials')
            port=u.port or (443 if u.scheme=='https' else 80)
            if port not in {80,443} or (u.scheme=='https' and port!=443) or (u.scheme=='http' and port!=80):raise PolicyBlocked('nonstandard_port')
            if host=='localhost' or host.endswith(('.localhost','.local','.internal','.lan','.home','.test','.invalid')):raise PolicyBlocked('local_or_reserved_hostname')
            if '%' in host:raise PolicyBlocked('zone_scoped_address')
            if any(re.search(r'^(?:access_?token|api_?key|token|password|secret|auth|authorization|cookie|session|signature)$',k,re.I) for k,v in parse_qsl(u.query,keep_blank_values=True)):raise PolicyBlocked('credential_like_URL_parameter')
            try:
                literal=ipaddress.ip_address(host)
                if not literal.is_global:raise PolicyBlocked('nonpublic_IP_literal')
            except ValueError:pass
            result=dict(host=host,port=port,scheme=u.scheme,path=(u.path or '/')+('?' + u.query if u.query else ''),ip=None)
            if resolve:
                ips=self.resolver(host,port)
                if not ips or any(not ipaddress.ip_address(x).is_global for x in ips):raise PolicyBlocked('DNS_nonpublic_or_mixed_answers')
                result['ip']=ips[0]
            return result
        except (ValueError,UnicodeError) as e:raise PolicyBlocked('invalid_URL_or_IP') from e

class NativeTransport:
    """No cookies, proxy env, auth, or automatic redirects. No DNS re-resolution."""
    def __init__(self,config):self.c=config
    def get_validated(self,url,limit,target):
        if not target.get('ip') or not ipaddress.ip_address(target['ip']).is_global:raise PolicyBlocked('unpinned_transport_target')
        cls=http.client.HTTPSConnection if target['scheme']=='https' else http.client.HTTPConnection
        deadline=time.monotonic()+self.c['response_deadline_sec']
        expired=threading.Event();active_socket=[None]
        kw=dict(timeout=min(self.c['connect_timeout_sec'],self.c['response_deadline_sec']))
        if target['scheme']=='https':kw['context']=ssl.create_default_context()
        conn=cls(target['host'],target['port'],**kw)
        conn._create_connection=lambda address,timeout,source_address=None:socket.create_connection((target['ip'],target['port']),timeout,source_address)
        def expire():
            expired.set()
            try:
                if active_socket[0] is not None:active_socket[0].shutdown(socket.SHUT_RDWR)
            except OSError:pass
            conn.close()
        def check():
            if expired.is_set() or time.monotonic()>=deadline:raise TimeoutError('absolute_response_deadline')
        timer=threading.Timer(max(0,deadline-time.monotonic()),expire);timer.daemon=True;timer.start()
        try:
            conn.connect();active_socket[0]=conn.sock;check();conn.sock.settimeout(min(self.c['read_timeout_sec'],max(.001,deadline-time.monotonic())))
            conn.request('GET',target['path'],headers={'User-Agent':self.c['user_agent'],'Accept':'text/html,application/xhtml+xml,text/plain;q=0.5','Accept-Encoding':'identity'})
            check();r=conn.getresponse();check();headers={k.lower():v for k,v in r.getheaders() if k.lower() in {'location','retry-after','content-type','content-length','content-encoding'}}
            if r.status in {301,302,303,307,308,401,403,429} or r.status>=500:return Response(url,r.status,headers,b'')
            if headers.get('content-length','').isdigit() and int(headers['content-length'])>limit:raise ReviewNeeded('response_size_limit')
            body=bytearray()
            while True:
                check();chunk=r.read1(min(65536,limit+1-len(body)));check()
                if not chunk:break
                body.extend(chunk)
                if len(body)>limit:raise ReviewNeeded('response_size_limit')
            if headers.get('content-length','').isdigit() and len(body)!=int(headers['content-length']):raise ReviewNeeded('incomplete_response_body')
            return Response(url,r.status,headers,bytes(body))
        except Exception as e:
            if 'r' in locals():e.http_status=r.status
            if expired.is_set() or time.monotonic()>=deadline:
                timeout=TimeoutError('absolute_response_deadline')
                if 'r' in locals():timeout.http_status=r.status
                raise timeout from e
            raise
        finally:
            timer.cancel()
            if 'r' in locals():r.close()
            conn.close()

class Gateway:
    def __init__(self,config,transport,guard,policies,now=time.time,sleep=time.sleep):
        self.c=config;self.transport=transport;self.guard=guard;self.policies=policies;self.now=now;self.sleep=sleep
        self.last={};self.failures={};self.cooldown={};self.blocked={};self.robots={};self.robot_intervals={};self.state_path=None;self.request_log=[]
        self._local=threading.local();self._shared=threading.RLock();self._domains={};self.writer=None
        self.context={};self.journal_path=None;self._request_id=0
    @property
    def context(self):
        if not hasattr(self._local,'context'):self._local.context={}
        return self._local.context
    @context.setter
    def context(self,value):self._local.context=value
    def domain_lock(self,host):
        with self._shared:return self._domains.setdefault(host,threading.RLock())
    def write(self,operation):
        if self.writer:self.writer(operation).result()
        else:operation()
    def bind_state(self,path):
        self.state_path=Path(path)
        self.journal_path=self.state_path.parent/'network_activity.jsonl'
        if self.journal_path.exists():
            with self.journal_path.open(encoding='utf-8') as f:
                self.request_log=[json.loads(line) for line in f if line.strip()]
        self._request_id=max((x.get('request_id',0) for x in self.request_log),default=0)
        if self.state_path.exists():
            d=jread(path)
            for k in ['last','failures','cooldown','blocked','robot_intervals']:setattr(self,k,d.get(k,{}))
    def persist(self):
        if self.state_path:
            # Snapshot on the manager, so an older worker cannot overwrite newer state.
            self.write(lambda:atomic_json(self.state_path,{k:getattr(self,k).copy() for k in ['last','failures','cooldown','blocked','robot_intervals']}))
    def record(self,**fields):
        event=dict(self.context,**fields,time=iso(self.now()))
        def commit():
            self.request_log.append(event)
            if self.journal_path:
                with self.journal_path.open('a',encoding='utf-8') as f:
                    f.write(json.dumps(event,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
        self.write(commit)
    def policy(self,host):
        if host in self.blocked:raise PolicyBlocked(self.blocked[host])
        p=self.policies.get(host,{})
        if not p.get('terms_reviewed'):raise ReviewNeeded('site_terms_not_reviewed:'+host)
        if not p.get('allowed'):raise PolicyBlocked('site_terms_not_permitted:'+host)
        if not p.get('evidence'):raise ReviewNeeded('site_terms_review_evidence_missing:'+host)
        if self.cooldown.get(host,0)>self.now():raise RetryLater('domain_circuit_or_cooldown',self.cooldown[host])
    def one(self,url,limit,kind):
        host=self.guard.check(url,resolve=False)['host']
        with self.domain_lock(host):return self._one(url,limit,kind)
    def _one(self,url,limit,kind):
        info=self.guard.check(url,resolve=False);host=info['host'];self.policy(host)
        try:target=self.guard.check(url)
        except (OSError,socket.gaierror) as e:
            self.record(kind='dns',phase='error',host=host,error_type=type(e).__name__)
            self.failure(host);raise RetryLater('DNS_or_connection_error',max(self.now()+2,self.cooldown.get(host,0))) from e
        delay=self.last.get(host,0)+max(2.,self.c['per_domain_min_interval_sec'],self.robot_intervals.get(host,0))-self.now()
        if delay>0:self.sleep(delay)
        self.last[host]=self.now();self.persist()
        with self._shared:
            self._request_id+=1;request_id=self._request_id
        self.record(kind=kind,phase='started',host=host,request_id=request_id,URL_sha256=hashlib.sha256(url.encode()).hexdigest())
        # Manager/extraction latency must not consume the next request's gap.
        # The host lock stays held from reservation through transport completion.
        self.last[host]=self.now()
        try:
            r=self.transport.get_validated(url,limit,target) if hasattr(self.transport,'get_validated') else self.transport.get(url,limit)
        except (OSError,http.client.HTTPException) as e:
            if getattr(e,'http_status',None):self.record(kind=kind,phase='response',host=host,request_id=request_id,http_status=e.http_status)
            self.record(kind=kind,phase='error',host=host,request_id=request_id,error_type=type(e).__name__)
            self.failure(host);raise RetryLater('connection_or_timeout',max(self.now()+self.c['backoff_base_sec'],self.cooldown.get(host,0))) from e
        except Exception as e:
            if getattr(e,'http_status',None):self.record(kind=kind,phase='response',host=host,request_id=request_id,http_status=e.http_status)
            self.record(kind=kind,phase='error',host=host,request_id=request_id,error_type=type(e).__name__)
            raise
        self.record(kind=kind,phase='response',host=host,request_id=request_id,http_status=r.status)
        self.persist()
        if r.status in {401,403}:
            self.blocked[host]=f'{kind}_HTTP_{r.status}';self.persist();raise PolicyBlocked(self.blocked[host])
        if r.status==429 or 500<=r.status<=599:
            delay=60. if r.status==429 else self.c['backoff_base_sec']
            try:
                value=float(r.headers.get('retry-after',str(delay)))
                if not math.isfinite(value):raise ValueError('nonfinite Retry-After')
                delay=max(2.,value)
            except ValueError:
                try:delay=max(2.,parsedate_to_datetime(r.headers['retry-after']).timestamp()-self.now())
                except Exception:pass
            self.cooldown[host]=max(self.cooldown.get(host,0),self.now()+delay);self.failure(host);self.persist()
            if delay>self.c['max_retry_after_sec']:raise ReviewNeeded('HTTP_'+str(r.status)+'_Retry_After_manual_review')
            raise RetryLater('HTTP_'+str(r.status),self.cooldown[host])
        if len(r.body)>limit:raise ReviewNeeded('response_size_limit')
        return r
    def failure(self,host):
        self.failures[host]=self.failures.get(host,0)+1
        if self.failures[host]>=self.c['circuit_failure_threshold']:self.cooldown[host]=max(self.cooldown.get(host,0),self.now()+self.c['circuit_cooldown_sec'])
        self.persist()
    def robots_allowed(self,url):
        host=self.guard.check(url,resolve=False)['host']
        with self.domain_lock(host):return self._robots_allowed(url)
    def _robots_allowed(self,url):
        u=urlsplit(url);origin=f'{u.scheme}://{u.netloc}';host=self.guard.check(url,resolve=False)['host']
        if origin not in self.robots:
            current=origin+'/robots.txt'
            for _ in range(self.c['max_redirects']+1):
                r=self.one(current,self.c['max_robots_bytes'],'robots')
                if r.status in {301,302,303,307,308}:
                    target=urljoin(current,r.headers.get('location',''));v=urlsplit(target)
                    if not r.headers.get('location') or (v.scheme,v.netloc)!=(u.scheme,u.netloc):raise ReviewNeeded('robots_cross_origin_redirect')
                    self.guard.check(target,resolve=False);current=target;continue
                break
            else:raise ReviewNeeded('robots_redirect_limit')
            if self.state_path:
                rpdir=self.state_path.parent/'robots';key=hashlib.sha256(origin.encode()).hexdigest()[:16]
                self.write(lambda:atomic_bytes(rpdir/(key+'.txt'),r.body))
                self.write(lambda:atomic_json(rpdir/(key+'.json'),dict(origin=origin,url=current,http_status=r.status,fetched_at=iso(self.now()),sha256=hashlib.sha256(r.body).hexdigest())))
            if r.status in {404,410}:self.robots[origin]=None
            elif r.status==200:
                if r.headers.get('content-encoding','identity').lower() not in {'identity',''}:raise ReviewNeeded('encoded_robots_response')
                self.robots[origin]=RobotsRules(r.body,self.c['robots_agent'])
            else:raise ReviewNeeded('robots_status_unknown_'+str(r.status))
        rp=self.robots[origin]
        if rp is not None:
            if not rp.allowed(url):raise PolicyBlocked('robots_disallowed')
            interval=rp.interval
            self.robot_intervals[host]=max(self.robot_intervals.get(host,0),interval);self.persist()
    def article(self,url):
        initial=url;seen=set()
        for hop in range(self.c['max_redirects']+1):
            self.context['redirect_hop']=hop
            info=self.guard.check(url,resolve=False);self.policy(info['host'])
            if url in seen:raise ReviewNeeded('redirect_loop')
            seen.add(url);self.robots_allowed(url);r=self.one(url,self.c['max_html_bytes'],'article')
            if r.status in {301,302,303,307,308}:
                if not r.headers.get('location'):raise ReviewNeeded('redirect_without_location')
                target=urljoin(url,r.headers['location']);self.guard.check(target,resolve=False)
                if urlsplit(url).scheme=='https' and urlsplit(target).scheme!='https':raise PolicyBlocked('HTTPS_downgrade_redirect')
                url=target;continue
            if r.status!=200:raise ReviewNeeded('HTTP_'+str(r.status))
            if r.headers.get('content-encoding','identity').lower() not in {'identity',''}:raise ReviewNeeded('encoded_HTML_response_not_processed')
            if r.headers.get('content-type','').split(';')[0].strip().lower() not in {'text/html','application/xhtml+xml'}:raise ReviewNeeded('non_HTML_response')
            if not re.search(br'<(?:!doctype\s+html|html|head|body|article)\b',r.body[:2048],re.I):raise ReviewNeeded('HTML_signature_unconfirmed')
            self.failures[info['host']]=0;self.persist();return r
        raise ReviewNeeded('article_redirect_limit')

def keyword_metrics(body,title=''):
    def normalize(s):
        s=re.sub(r'[;.!?]+',' | ',unicodedata.normalize('NFKC',s).lower())
        return re.sub(r'[^\w|]+',' ',s).strip()
    b=normalize(body);t=normalize(title)
    patterns={'strait_of_hormuz_count':r'\bstrait of hormuz\b','hormuz_strait_count':r'\bhormuz strait\b','straits_of_hormuz_count':r'\bstraits of hormuz\b','hormuz_straits_count':r'\bhormuz straits\b'}
    counts={k:0 for k in patterns}
    combined='|'.join(f'(?P<{k}>{pattern})' for k,pattern in patterns.items())
    for match in re.finditer(combined,b):counts[match.lastgroup]+=1
    total=len(re.findall(r'\bhormuz\b',b));explicit=sum(counts.values())
    return dict(**counts,hormuz_count=total,hormuz_only_count=max(0,total-explicit),body_keyword_present=bool(total),title_keyword_present=bool(re.search(r'\bhormuz\b',t)),
        body_relevance='explicit_strait_mention' if explicit else 'hormuz_only' if total else 'no_body_keyword')
def body_hash(body):return hashlib.sha256(re.sub(r'\s+',' ',body).strip().encode('utf-8')).hexdigest()
def simhash(body):
    counts=collections.Counter(re.findall(r'\b\w+\b',body.lower()));v=[0]*64
    for w,n in counts.items():
        h=int.from_bytes(hashlib.blake2b(w.encode(),digest_size=8).digest(),'big')
        for i in range(64):v[i]+=n if h>>i&1 else -n
    return sum(1<<i for i,x in enumerate(v) if x>=0)
def quality_metrics(body,title,config):
    words=re.findall(r'\b\w+\b',body);reasons=[];language='unknown';probability=None
    if not body.strip():reasons.append('empty_body')
    if len(words)<config['short_body_words_review']:reasons.append('short_body_review')
    if sum(c.isalpha() for c in body)>=30:
        try:
            from langdetect import DetectorFactory,detect_langs
            DetectorFactory.seed=config['language_seed'];pred=detect_langs(body[:10000])[0];language=pred.lang;probability=pred.prob
        except Exception:reasons.append('language_detection_unavailable_or_failed')
    if language!='en' or probability is None or probability<config['language_probability_review']:reasons.append('language_review')
    kw=keyword_metrics(body,title)
    if not kw['body_keyword_present']:reasons.append('no_body_keyword')
    elif kw['body_relevance']=='hormuz_only':reasons.append('hormuz_only_context_review')
    pollution=[]
    for code,pattern in {'comments_UI':r'\b(?:sign in to comment|leave a comment|reader comments)\b','recommendation_UI':r'\b(?:recommended stories|related articles|latest trending)\b','newsletter_UI':r'\bsubscribe to (?:our|the) newsletter\b'}.items():
        if re.search(pattern,body,re.I):pollution.append(code)
    if pollution:reasons.append('possible_boilerplate_or_comment_contamination')
    return dict(**kw,chars=len(body),words=len(words),language=language,language_probability=probability,language_threshold=config['language_probability_review'],language_basis='langdetect seed=0; heuristic not calibrated truth',
        is_English_body='unknown' if language=='unknown' else '1' if language=='en' else '0',contamination_hints=pollution,
        review_reasons=reasons,body_sha256=hashlib.sha256(body.encode()).hexdigest(),normalized_body_sha256=body_hash(body),simhash64=str(simhash(body)),
        duplicate_body_ids=[],near_duplicate_body_ids=[],human_review_required=True,human_final_label='',
        quality_status='review' if reasons else 'automated_checks_passed_human_unconfirmed')

def access_check(html):
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(html,'html.parser')
    for x in soup(['script','style','noscript']):
        if x.name=='script' and x.get('type')=='application/ld+json' and re.search(r'"isAccessibleForFree"\s*:\s*(?:false|"false")',x.get_text(),re.I):return 'schema_paid_signal_review'
        x.decompose()
    for node in soup.select('[id*="paywall"], [class*="paywall"], [id*="login-wall"], [class*="login-wall"], [data-testid*="paywall"]'):
        if node.has_attr('hidden') or re.search(r'display\s*:\s*none',node.get('style',''),re.I):continue
        text=node.get_text(' ',strip=True).lower()
        if re.search(r'(?:sign|log) in to (?:continue|read)',text):return 'login_gate_detected'
        if re.search(r'(?:subscribe|subscription|purchase|pay) (?:to |is required to |required to )?(?:continue|read|access)|subscriber.only|subscribers.only',text):return 'paid_or_partial_gate_detected'
    # Explicit gating text without structural confirmation is review, not paid fact.
    visible=soup.get_text(' ',strip=True)
    if re.search(r'(?:subscribe|sign in|log in) to continue reading',visible,re.I):return 'gate_text_signal_review'
    return 'no_gate_detected_not_verified'

def extraction_backend(html,url,kind):
    if str(VENDOR) not in sys.path:sys.path.insert(0,str(VENDOR))
    if kind.startswith('trafilatura'):
        import trafilatura
        raw=trafilatura.extract(html,url=url,output_format='json',with_metadata=True,include_comments=False,favor_precision=kind.endswith('precision'))
        d=json.loads(raw) if raw else {};return dict(body=d.get('text') or '',title=d.get('title') or '')
    from newsplease import NewsPlease
    obj=NewsPlease.from_html(html,url=url,fetch_images=False)
    return dict(body=obj.maintext or '',title=obj.title or '') if obj else dict(body='',title='')
def extract_html(html,url,title,config,backend=None):
    # newspaper reads gettempdir during its first import. Resolve an existing
    # directory without tempfile's write-probe; all extractor writes stay denied.
    old_tempdir=tempfile.tempdir
    if old_tempdir is None:
        tempfile.tempdir=next((str(Path(p).resolve()) for p in [os.getenv('TMPDIR'),os.getenv('TEMP'),os.getenv('TMP'),str(ROOT)] if p and Path(p).is_dir()),str(ROOT))
    backend=backend or extraction_backend;results={};token=_NO_NETWORK.set(True);write_token=_EXTRACTING.set(True)
    try:
        kinds=['trafilatura_default']+(['trafilatura_precision'] if config['compare_precision'] else [])
        for kind in kinds:
            try:results[kind]=dict(status='text_extracted',**backend(html,url,kind))
            except Exception as e:results[kind]=dict(status='extractor_error',body='',title='',error_type=type(e).__name__)
        primary=results['trafilatura_default']['body'];quality=quality_metrics(primary,title,config)
        ta=set(re.findall(r'\w+',title.lower()));tb=set(re.findall(r'\w+',results['trafilatura_default'].get('title','').lower()))
        if len(ta)>=4 and (not tb or len(ta&tb)/len(ta|tb)<.2):quality['review_reasons'].append('title_alignment_review')
        if quality['review_reasons'] or any(x['body']!=primary for k,x in results.items() if k!='trafilatura_default'):
            try:results['newsplease']=dict(status='text_extracted',**backend(html,url,'newsplease'))
            except Exception as e:results['newsplease']=dict(status='extractor_error',body='',title='',error_type=type(e).__name__)
        chosen='trafilatura_default'
        if not primary.strip() and results.get('newsplease',{}).get('body','').strip():
            chosen='newsplease';quality=quality_metrics(results[chosen]['body'],title,config);quality['review_reasons'].append('fallback_used_review')
        for d in results.values():
            if d['status']!='extractor_error' and not d['body'].strip():d['status']='empty'
            d['chars']=len(d['body']);d['words']=len(d['body'].split())
        for kind,d in results.items():
            try:d['version']=importlib.metadata.version('trafilatura' if kind.startswith('trafilatura') else 'news-please')
            except importlib.metadata.PackageNotFoundError:d['version']='unknown'
        if config['compare_precision'] and results['trafilatura_precision']['body']!=primary:quality['review_reasons'].append('default_precision_difference_review')
        # Title alignment is a heuristic against stored target metadata, not proof.
        a=set(re.findall(r'\w+',title.lower()));b=set(re.findall(r'\w+',results[chosen].get('title','').lower()))
        quality['metadata_extracted_title_Jaccard']=len(a&b)/len(a|b) if a|b else None
        if len(a)>=4 and (not b or quality['metadata_extracted_title_Jaccard']<.2) and 'title_alignment_review' not in quality['review_reasons']:quality['review_reasons'].append('title_alignment_review')
        quality['quality_status']='review' if quality['review_reasons'] else 'automated_checks_passed_human_unconfirmed'
        return dict(chosen_extractor=chosen,body=results[chosen]['body'],extractors=results,quality=quality)
    finally:_EXTRACTING.reset(write_token);_NO_NETWORK.reset(token);tempfile.tempdir=old_tempdir

STATE_FIELDS=['article_id','pilot_order','URL','domain','status','attempts','last_request_at','next_retry_at','http_status','extractor','error_type','html_path','result_path']
class StateStore:
    def __init__(self,out,rows):
        self.out=Path(out);self.rows=rows;self.path=self.out/'pilot_state.csv'
        if len(rows)!=200 or len({r['article_id'] for r in rows})!=200 or len({r['request_url'] for r in rows})!=200:raise ValueError('Exactly200 unique pilot IDs/URLs required')
        if any(not re.fullmatch('[a-f0-9]{16}',r['article_id']) for r in rows):raise ValueError('Unsafe article ID')
        if [int(r['pilot_order']) for r in rows]!=list(range(1,201)):raise ValueError('Pilot order must be 1..200')
    def initialize(self):
        if self.path.exists():raise FileExistsError('State already exists; never reset')
        rows=[{k:'' for k in STATE_FIELDS} for r in self.rows]
        for state,r in zip(rows,self.rows):state.update(article_id=r['article_id'],pilot_order=r['pilot_order'],URL=r['request_url'],domain=r['domain'],status='pending',attempts='0')
        self.save(rows)
    def load(self):
        rows=csvread(self.path)
        if len(rows)!=200 or [(r['article_id'],r['URL'],r['pilot_order']) for r in rows]!=[(r['article_id'],r['request_url'],r['pilot_order']) for r in self.rows]:raise ValueError('State identity/order differs from locked queue')
        if any(r['status'] not in {'pending','running','done','review','error','policy_blocked'} for r in rows):raise ValueError('Unknown state')
        return rows
    def save(self,rows):csvwrite(self.path,rows,STATE_FIELDS)
    def recover_running(self):
        rows=self.load()
        for r in rows:
            if r['status']!='running':continue
            result=self.out/'results'/f'{r["article_id"]}.json';provenance=self.out/'html'/f'{r["article_id"]}.json'
            if result.exists():
                d=jread(result)
                if d.get('article_id')!=r['article_id'] or d.get('request_url')!=r['URL']:raise ValueError('Interrupted result identity mismatch')
                if d.get('state') in TERMINAL and str(d.get('attempts'))==r['attempts']:
                    html=self.out/'html'/f'{r["article_id"]}.html'
                    if d.get('html_sha256') and (not html.exists() or sha(html)!=d['html_sha256']):raise ValueError('Recovered HTML hash mismatch')
                    if d.get('chosen_text_path'):
                        text=(self.out/d['chosen_text_path']).resolve()
                        if not text.is_relative_to(self.out.resolve()) or sha(text)!=d['quality']['body_sha256']:raise ValueError('Recovered text path/hash mismatch')
                    r.update(status=d['state'],result_path=str(result.relative_to(self.out)),error_type='recovered_saved_result');continue
            if provenance.exists() and (self.out/'html'/f'{r["article_id"]}.html').exists():r.update(status='pending',error_type='reuse_saved_HTML_no_refetch')
            else:r.update(status='review',error_type='interrupted_request_outcome_unknown_no_refetch',next_retry_at='')
        self.save(rows)

def outcome_record(result,state,activity):
    """Observation flags, not a human ground truth or a corpus inclusion rule."""
    q=result.get('quality',{});body=bool(result.get('body_extraction_success'))
    attempted=int(state.get('attempts') or 0)>0 or bool(result)
    en='unknown'
    if body and q.get('language') not in {None,'unknown'} and q.get('language_probability') is not None and q['language_probability']>=q.get('language_threshold',.8):
        en='1' if q['language']=='en' else '0'
    duplicate=bool(q.get('duplicate_body_ids') or q.get('near_duplicate_body_ids'))
    return dict(article_id=state['article_id'],pilot_order=state['pilot_order'],URL=state['URL'],domain=state['domain'],state=state['status'],
        processing_attempts=int(state.get('attempts') or 0),
        article_HTTP_started=any(x.get('phase')=='started' and x.get('kind')=='article' for x in activity),
        article_HTTP_200=bool(result.get('http_access_success')) or any(x.get('phase')=='response' and x.get('kind')=='article' and x.get('http_status')==200 for x in activity),
        HTML_saved=bool(result.get('html_saved')),nonempty_body=body,chars=q.get('chars',0),words=q.get('words',0),
        English_auto=en,language=q.get('language','unknown'),language_probability=q.get('language_probability'),
        body_keyword_present=q.get('body_keyword_present','unknown') if body else 'unknown',hormuz_count=q.get('hormuz_count',0),
        explicit_strait_mention=(q.get('body_relevance')=='explicit_strait_mention') if body else 'unknown',
        explicit_strait_count=sum(q.get(k,0) for k in ['strait_of_hormuz_count','hormuz_strait_count','straits_of_hormuz_count','hormuz_straits_count']),
        short_body_suspected=('short_body_review' in q.get('review_reasons',[])) if body else 'unknown',
        access_status=result.get('access_status','unknown'),gate_suspected=result.get('access_status','unknown') not in {'unknown','no_gate_detected_not_verified'},
        duplicate_suspected=duplicate,duplicate_ids=q.get('duplicate_body_ids',[]),near_duplicate_ids=q.get('near_duplicate_body_ids',[]),
        automated_quality_pass=(body and en=='1' and not q.get('review_reasons')) if q else 'unknown',
        human_review_required=True if attempted else 'unknown',review_reasons=q.get('review_reasons',[]) or ([state.get('error_type')] if state.get('error_type') else []),
        human_final_label='',html_path=result.get('html_path',''),text_path=result.get('chosen_text_path',''))

def article_outcomes(out,rows):
    out=Path(out);states=StateStore(out,rows).load();journal=out/'network_activity.jsonl';activity=[]
    if journal.exists():
        with journal.open(encoding='utf-8') as f:activity=[json.loads(line) for line in f if line.strip()]
    by_id=collections.defaultdict(list)
    for a in activity:by_id[a.get('article_id')].append(a)
    projected=[]
    for state,meta in zip(states,rows):
        path=out/'results'/f'{state["article_id"]}.json';r=jread(path) if path.exists() else {}
        if r and (r.get('article_id')!=state['article_id'] or r.get('request_url')!=meta['request_url']):raise ValueError('Summary result identity mismatch')
        projected.append(outcome_record(r,state,by_id[state['article_id']]))
    # Symmetric group membership is projected without rewriting older results.
    linked=set()
    for r in projected:
        if r['duplicate_suspected']:linked.update([r['article_id'],*r['duplicate_ids'],*r['near_duplicate_ids']])
    for r in projected:
        if r['article_id'] in linked and r['nonempty_body']:
            r['duplicate_suspected']=True;r['automated_quality_pass']=False
            if 'duplicate_or_near_duplicate_body_review' not in r['review_reasons']:r['review_reasons'].append('duplicate_or_near_duplicate_body_review')
    return projected

def summarize_run(out,rows):
    outcomes=article_outcomes(out,rows);n=len(outcomes);counts={}
    count=lambda predicate:sum(bool(predicate(x)) for x in outcomes)
    attempted=count(lambda x:x['processing_attempts']>0);started=count(lambda x:x['article_HTTP_started'])
    html=count(lambda x:x['article_HTTP_200'] and x['HTML_saved']);body=count(lambda x:x['nonempty_body'])
    def metric(name,value,denominator,basis):
        counts[name]=dict(count=value,denominator=denominator,denominator_basis=basis,ratio=value/denominator if denominator else None)
    metric('processing_attempted_URLs',attempted,n,'fixed selected URLs')
    metric('article_HTTP_started_URLs',started,n,'fixed selected URLs; transport invoked, not proof of wire delivery')
    http200=count(lambda x:x['article_HTTP_200'])
    metric('HTTP200_URLs',http200,started,'URLs with article transport started')
    metric('HTML_saved_URLs',count(lambda x:x['HTML_saved']),http200,'URLs with observed article HTTP200')
    metric('HTTP200_HTML_saved_URLs',html,started,'URLs with article transport started')
    metric('nonempty_body_URLs',body,html,'HTTP200 and HTML saved URLs')
    for name,predicate in [('English_auto_bodies',lambda x:x['English_auto']=='1'),('Hormuz_body_URLs',lambda x:x['body_keyword_present'] is True),('explicit_strait_body_URLs',lambda x:x['explicit_strait_mention'] is True),('automatic_quality_pass_URLs',lambda x:x['automated_quality_pass'] is True),('duplicate_suspected_URLs',lambda x:x['duplicate_suspected'])]:
        metric(name,count(predicate),body,'nonempty extracted body URLs')
    metric('human_review_URLs',count(lambda x:x['human_review_required'] is True),attempted,'processing-attempted URLs; AI is not human truth')
    for name,predicate in [('policy_blocked_URLs',lambda x:x['state']=='policy_blocked'),('technical_error_URLs',lambda x:x['state']=='error'),('review_state_URLs',lambda x:x['state']=='review'),('unattempted_URLs',lambda x:x['processing_attempts']==0 and x['state']=='pending')]:metric(name,count(predicate),n,'fixed selected URLs')
    events=[];journal=Path(out)/'network_activity.jsonl'
    if journal.exists():
        with journal.open(encoding='utf-8') as f:events=[json.loads(line) for line in f if line.strip()]
    requests={k:sum(x.get('kind')==k and x.get('phase')=='started' for x in events) for k in ['robots','article']}
    requests=dict(robots_started=requests['robots'],article_started=requests['article'],responses=sum(x.get('phase')=='response' for x in events),
        transport_errors=sum(x.get('phase')=='error' and x.get('kind')!='dns' for x in events),
        connection_errors=sum(x.get('phase')=='error' and x.get('error_type') in {'TimeoutError','OSError','ConnectionError','ConnectionResetError','ConnectionRefusedError','RemoteDisconnected','BadStatusLine','IncompleteRead'} for x in events),DNS_errors=sum(x.get('kind')=='dns' for x in events),
        redirect_responses=sum(x.get('phase')=='response' and x.get('http_status') in {301,302,303,307,308} for x in events),
        robots_redirect_responses=sum(x.get('kind')=='robots' and x.get('phase')=='response' and x.get('http_status') in {301,302,303,307,308} for x in events),
        article_redirect_responses=sum(x.get('kind')=='article' and x.get('phase')=='response' and x.get('http_status') in {301,302,303,307,308} for x in events),
        retry_started=sum(x.get('phase')=='started' and int(x.get('logical_attempt',0))>1 for x in events),
        total_started=sum(x.get('phase')=='started' for x in events),unfinished_started_requests=sum(x.get('phase')=='started' and not any(y.get('request_id')==x.get('request_id') and y.get('phase') in {'response','error'} for y in events) for x in events))
    return dict(schema_version='pilot_metrics_v2',selected_URLs=n,metrics=counts,HTTP_request_counts=requests,
        interpretation='Stage denominators differ. done/review are processing states, not valid corpus counts. Human final labels remain blank.',
        requests_basis='Durable transport-start records; connection failure can precede wire HTTP. Started without completion has unknown outcome; never assumed successful.')

def write_summary(out,rows):
    out=Path(out);projected=article_outcomes(out,rows)
    for r in projected:
        for k,v in list(r.items()):
            if isinstance(v,list):r[k]=json.dumps(v,ensure_ascii=False)
    csvwrite(out/'pilot_outcomes.csv',projected);atomic_json(out/'pilot_summary.json',summarize_run(out,rows))

class Engine:
    def __init__(self,out,rows,config,gateway,store=None,extractor=extract_html,now=time.time):
        self.out=Path(out);self.rows={r['article_id']:r for r in rows};self.c=config;self.gateway=gateway;self.store=store or StateStore(out,rows);self.extractor=extractor;self.now=now
        self.fingerprints=[]
        for p in (self.out/'results').glob('*.json') if (self.out/'results').exists() else []:
            d=jread(p)
            if d.get('quality'):self.fingerprints.append((d['article_id'],d['quality']))
        if hasattr(gateway,'bind_state'):gateway.bind_state(self.out/'network_policy_state.json')
    def step(self,aid,prepared=None):
        if aid not in self.rows:raise ValueError('Article not in fixed200 allowlist')
        allstate=self.store.load();state=next(r for r in allstate if r['article_id']==aid);meta=self.rows[aid]
        if state['status'] in TERMINAL or (state['status']=='running' and prepared is None):return
        if state['next_retry_at'] and timestamp(state['next_retry_at'])>self.now():return
        if state['status']=='error' and not state['next_retry_at']:return
        hp=self.out/'html'/f'{aid}.html';prov=hp.with_suffix('.json');resultpath=self.out/'results'/f'{aid}.json'
        if prepared is None and not hp.exists() and int(state['attempts'])>=1+self.c['max_retries']:return
        state['status']='running';self.store.save(allstate)
        result=dict(article_id=aid,pilot_order=meta['pilot_order'],request_url=meta['request_url'],domain=meta['domain'],http_access_success=False,html_saved=False,body_extraction_success=False,title=meta.get('title',''),title_dup_group_id=meta.get('title_dup_group_id',''),title_dup_group_size=meta.get('title_dup_group_size',''),human_final_label='')
        committed_result=False
        try:
            if hp.exists()!=prov.exists():raise ReviewNeeded('orphan_HTML_or_provenance_review_no_refetch')
            if hp.exists() and prov.exists():
                evidence=jread(prov)
                if evidence['article_id']!=aid or evidence['request_url']!=meta['request_url'] or sha(hp)!=evidence['html_sha256']:raise ValueError('Saved HTML identity/hash mismatch')
                response=Response(evidence['final_url'],evidence['http_status'],{'content-type':'text/html'},hp.read_bytes())
            else:
                if prepared is None:
                    state['attempts']=str(int(state['attempts'])+1);state['last_request_at']=iso(self.now());self.store.save(allstate)
                    if hasattr(self.gateway,'context'):self.gateway.context=dict(article_id=aid,logical_attempt=int(state['attempts']))
                    response=self.gateway.article(meta['request_url'])
                else:
                    response,error=prepared
                    if error is not None:raise error
                hp.parent.mkdir(parents=True,exist_ok=True)
                with hp.open('xb') as f:f.write(response.body)
                evidence=dict(article_id=aid,request_url=meta['request_url'],final_url=response.url,http_status=response.status,fetched_at=state['last_request_at'],html_sha256=sha(hp))
                atomic_json(prov,evidence)
            state.update(http_status=str(response.status),html_path=str(hp.relative_to(self.out)));result.update(http_access_success=True,html_saved=True,html_path=state['html_path'],html_sha256=sha(hp),final_url=response.url)
            access=access_check(response.body);result['access_status']=access
            if access!='no_gate_detected_not_verified':
                state.update(status='policy_blocked' if access in {'paid_or_partial_gate_detected','login_gate_detected'} else 'review',error_type=access)
            else:
                extracted=self.extractor(response.body,response.url,meta.get('title',''),self.c);q=extracted['quality'];chosen=extracted['chosen_extractor']
                for previous,pq in self.fingerprints:
                    if previous==aid:continue
                    if q['normalized_body_sha256']==pq['normalized_body_sha256'] and q['words']>0:q['duplicate_body_ids'].append(previous)
                    elif min(q['words'],pq['words'])>=self.c['near_duplicate_min_words'] and (int(q['simhash64'])^int(pq['simhash64'])).bit_count()<=self.c['near_duplicate_simhash_distance']:q['near_duplicate_body_ids'].append(previous)
                if q['duplicate_body_ids'] or q['near_duplicate_body_ids']:q['review_reasons'].append('duplicate_or_near_duplicate_body_review')
                q['quality_status']='review' if q['review_reasons'] else 'automated_checks_passed_human_unconfirmed'
                for kind,d in extracted['extractors'].items():
                    p=self.out/'texts'/f'{aid}.{kind}.txt';p.parent.mkdir(parents=True,exist_ok=True)
                    if p.exists() and sha(p)!=hashlib.sha256(d['body'].encode()).hexdigest():raise ValueError('Refuse overwrite of differing extractor text')
                    if not p.exists():p.write_bytes(d['body'].encode('utf-8'))
                    d['text_path']=str(p.relative_to(self.out));d.pop('body',None)
                result.update(quality=q,chosen_extractor=chosen,extractors=extracted['extractors'],body_extraction_success=bool(extracted['body'].strip()),chosen_text_path=f'texts/{aid}.{chosen}.txt')
                state.update(status='review' if q['review_reasons'] else 'done',extractor=chosen,error_type=';'.join(q['review_reasons']))
                self.fingerprints.append((aid,q))
            state.update(result_path=str(resultpath.relative_to(self.out)),next_retry_at='')
            result.update(state=state['status'],attempts=state['attempts'],http_status=state['http_status'],last_request_at=state['last_request_at'])
            atomic_json(resultpath,result);committed_result=True
        except PolicyBlocked as e:state.update(status='policy_blocked',error_type=str(e),next_retry_at='')
        except ReviewNeeded as e:state.update(status='review',error_type=str(e),next_retry_at='')
        except RetryLater as e:
            due=max(e.when,self.now()+self.c['backoff_base_sec']*2**max(0,int(state['attempts'])-1))
            state.update(status='error',error_type=str(e),next_retry_at=iso(due) if int(state['attempts'])<=self.c['max_retries'] else '')
        except Exception as e:state.update(status='error',error_type='processing_'+type(e).__name__,next_retry_at='')
        finally:
            activity=getattr(self.gateway,'request_log',[])
            own_activity=[x for x in activity if x.get('article_id')==aid]
            if any(x.get('kind')=='article' and x.get('phase')=='response' and x.get('http_status')==200 for x in own_activity):result['http_access_success']=True
            latest=[x['http_status'] for x in own_activity if x.get('kind')=='article' and x.get('phase')=='response']
            if latest:state['http_status']=str(latest[-1]);result['http_status']=state['http_status']
            code=re.search(r'HTTP_(\d{3})',state['error_type'])
            if code:state['http_status']=code.group(1)
            if not committed_result and state['status']!='running':
                result.update(state=state['status'],http_status=state['http_status'],error_type=state['error_type'],attempts=state['attempts'])
                state['result_path']=str(resultpath.relative_to(self.out));atomic_json(resultpath,result)
            if self.gateway is not None and hasattr(self.gateway,'request_log'):
                result['HTTP_response_activity']=[x for x in own_activity if int(x.get('logical_attempt',0))==int(state['attempts'])]
                result['logical_attempts']=state['attempts']
                atomic_json(self.out/'request_activity.json',dict(requests=self.gateway.request_log,scope='cumulative durable lifecycle journal; starts, responses, errors separate'))
            result['outcome']=outcome_record(result,state,own_activity)
            if state['status']!='running':atomic_json(resultpath,result)
            self.store.save(allstate)
            with (self.out/'events.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(dict(article_id=aid,pilot_order=meta['pilot_order'],status=state['status'],attempts=state['attempts'],error_type=state['error_type'],timestamp=iso(self.now())),ensure_ascii=False)+'\n')

    def progress(self):
        states=self.store.load();counts=collections.Counter(s['status'] for s in states)
        bodies=sum(bool(jread(self.out/s['result_path']).get('body_extraction_success')) for s in states if s['result_path'] and (self.out/s['result_path']).exists())
        return dict(total=len(states),completed=sum(counts[s] for s in TERMINAL),failed=counts['error'],pending=counts['pending'],in_progress=counts['running'],body_acquired=bodies)

    def run_parallel(self,workers=4,progress=None):
        """Workers fetch only; this calling thread commits all files and extracts.

        One bounded pass; retries not yet due remain untouched. On Ctrl-C stop
        scheduling, drain already submitted work, then propagate the interrupt.
        """
        if not isinstance(workers,int) or workers<1:raise ValueError('workers must be a positive integer')
        progress=progress or (lambda data:print(json.dumps(dict(progress=data)),flush=True))
        writes=queue.Queue();active={};remaining=iter(self.rows);exhausted=False;interrupted=False
        previous_writer=self.gateway.writer
        def post(operation):
            future=concurrent.futures.Future();writes.put((operation,future));return future
        def pump():
            while True:
                try:operation,future=writes.get_nowait()
                except queue.Empty:break
                try:future.set_result(operation())
                except BaseException as error:future.set_exception(error)
        def fetch(aid,attempt):
            self.gateway.context=dict(article_id=aid,logical_attempt=attempt)
            try:return self.gateway.article(self.rows[aid]['request_url']),None
            except KeyboardInterrupt:return None,ReviewNeeded('worker_interrupted_outcome_unknown_no_refetch')
            except Exception as error:return None,error
        self.gateway.writer=post
        try:
            progress(self.progress())
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers,thread_name_prefix='body-pilot') as pool:
                while active or not exhausted:
                    try:
                        while not interrupted and not exhausted and len(active)<workers:
                            aid=next(remaining,None)
                            if aid is None:exhausted=True;break
                            states=self.store.load();s=next(s for s in states if s['article_id']==aid)
                            if s['status'] in TERMINAL or s['status']=='running':continue
                            if s['next_retry_at'] and timestamp(s['next_retry_at'])>self.now():continue
                            if s['status']=='error' and not s['next_retry_at']:continue
                            hp=self.out/'html'/f'{aid}.html'
                            if hp.exists() or hp.with_suffix('.json').exists():
                                self.step(aid);progress(self.progress());continue
                            if int(s['attempts'])>=1+self.c['max_retries']:continue
                            s.update(status='running',attempts=str(int(s['attempts'])+1),last_request_at=iso(self.now()))
                            self.store.save(states)
                            context=contextvars.copy_context()
                            active[pool.submit(context.run,fetch,aid,int(s['attempts']))]=aid
                            progress(self.progress())
                        pump()
                        done,_=concurrent.futures.wait(active,timeout=.02,return_when=concurrent.futures.FIRST_COMPLETED) if active else (set(),set())
                        for future in done:
                            aid=active[future]
                            self.step(aid,prepared=future.result());del active[future]
                            if not interrupted:progress(self.progress())
                    except KeyboardInterrupt:
                        interrupted=True;exhausted=True
                    except BaseException:
                        # Keep servicing durable network writes while workers finish;
                        # leave interrupted state for recover_running(), never refetch.
                        while any(not f.done() for f in active):
                            pump();concurrent.futures.wait(active,timeout=.02)
                        pump();raise
            pump()
            if interrupted:raise KeyboardInterrupt
        finally:self.gateway.writer=previous_writer

def validate_approval(d,queue_sha,rows):
    if d.get('approved') is not True or d.get('queue_sha256')!=queue_sha or d.get('article_count')!=200 or not d.get('approved_by') or not d.get('approved_at'):
        raise PolicyBlocked('Later explicit user approval bound to fixed200 queue is required')
    try:approved_at=datetime.fromisoformat(d['approved_at'])
    except Exception as e:raise PolicyBlocked('Invalid approval timestamp') from e
    if approved_at.tzinfo is None:raise PolicyBlocked('Approval must have explicit timezone')
    return d.get('domain_policies',{})
def load_locked_pilot(out):
    out=Path(out).resolve();lock=jread(out/'pilot_lock.json')
    for name,h in lock['immutable_hashes'].items():
        if Path(name).name!=name or sha(out/name)!=h:raise PolicyBlocked('Frozen pilot input hash mismatch:'+name)
    for name,h in lock['code_hashes'].items():
        p=(ROOT/name).resolve()
        if not p.is_relative_to(ROOT/'scripts') or sha(p)!=h:raise PolicyBlocked('Runner code hash differs from prepared version')
    rows=csvread(out/'pilot_200_queue.csv');StateStore(out,rows).load()
    for r in rows:
        info=URLGuard().check(r['request_url'],resolve=False)
        if info['host']!=r['domain']:raise PolicyBlocked('Queue hostname differs from original domain')
    return rows,jread(out/'pilot_config.json')

def main():
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--execute',action='store_true');p.add_argument('--approval-file',type=Path);p.add_argument('--workers',type=int,default=4);args=p.parse_args()
    if args.workers<1:p.error('--workers must be positive')
    token=_NO_NETWORK.set(not args.execute)
    try:
        if not args.run_dir.resolve().is_relative_to(ROOT/'hormuz_2026_data/runs') or not args.run_dir.name.startswith('body_pilot_200_'):raise PolicyBlocked('Runtime output must be a dedicated body_pilot_200 run')
        rows,c=load_locked_pilot(args.run_dir)
        if not args.execute:
            print(json.dumps(dict(mode='offline_dry_run',URL_count=len(rows),states=dict(collections.Counter(r['status'] for r in StateStore(args.run_dir,rows).load())),HTTP_requests=0,HTML_downloads=0,actual_news_extractions=0,summary=summarize_run(args.run_dir,rows)),ensure_ascii=False,indent=2));return
        if not args.approval_file:raise PolicyBlocked('--approval-file required; preparation is not execution approval')
        policies=validate_approval(jread(args.approval_file),sha(args.run_dir/'pilot_200_queue.csv'),rows)
        lock=args.run_dir/'.execution.lock'
        try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        except FileExistsError:raise PolicyBlocked('Execution lock exists; inspect PID and interrupt checkpoint before manual recovery')
        try:
            with os.fdopen(fd,'w') as f:f.write(json.dumps(dict(pid=os.getpid(),started_at=iso(time.time()))))
            gateway=Gateway(c,NativeTransport(c),URLGuard(),policies);store=StateStore(args.run_dir,rows);store.recover_running();engine=Engine(args.run_dir,rows,c,gateway,store=store)
            # One bounded pass. Future due retries need a separate resumed pass.
            engine.run_parallel(workers=args.workers)
            print(json.dumps(dict(mode='fixed200_one_pass_complete',states=dict(collections.Counter(r['status'] for r in store.load()))),ensure_ascii=False,indent=2))
        finally:
            try:write_summary(args.run_dir,rows)
            finally:lock.unlink()
    finally:_NO_NETWORK.reset(token)
if __name__=='__main__':main()
