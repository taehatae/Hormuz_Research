"""Additional regression cases: synthetic fixtures, fake clocks/DNS/transport only."""
import unittest,json,socket
from unittest.mock import patch
from pathlib import Path
import body_pilot_200 as p
from test_body_pilot_200 import queue,gateway,HTML,Clock
import test_body_pilot_200 as fixtures

class RobotsRegression(unittest.TestCase):
    def test_truncated_declared_response_is_not_valid_HTML(self):
        class Timer:
            def __init__(self,*args):pass
            def start(self):pass
            def cancel(self):pass
        class Reply:
            status=200
            def getheaders(self):return [('Content-Type','text/html'),('Content-Length','100')]
            def read1(self,n):return b''
            def close(self):pass
        class Conn:
            def __init__(self,*args,**kw):self.sock=self
            def connect(self):pass
            def settimeout(self,t):pass
            def request(self,*a,**kw):pass
            def getresponse(self):return Reply()
            def close(self):pass
        with patch('body_pilot_200.threading.Timer',Timer),patch('http.client.HTTPConnection',Conn),self.assertRaises(p.ReviewNeeded):
            p.NativeTransport(p.default_config()).get_validated('http://example.org/mock',1000,dict(host='example.org',port=80,scheme='http',path='/mock',ip='93.184.216.34'))
    def test_unreviewed_or_denied_terms_before_DNS(self):
        for policy,exc in [({},p.ReviewNeeded),({'terms_reviewed':True,'allowed':False},p.PolicyBlocked)]:
            g,t,c=gateway([]);g.policies={'example.org':policy};calls=[]
            g.guard=p.URLGuard(resolver=lambda h,port:calls.append(h))
            with self.assertRaises(exc):g.article('https://example.org/mock/1')
            self.assertEqual(calls,[]);self.assertEqual(t.calls,[])
    def test_uncertain_robots_never_allow(self):
        for body in [b'not a robots file',b'Disallow: /',b'User-agent: *\nCrawl-delay: many',b'User-agent: *\nRequest-rate: 0/0',b'User-agent: *\nUnknown-directive: yes',b'<!DOCTYPE html><title>Forbidden</title>',b'User-agent: *\nDisallow /',b'\xff\xfe']:
            with self.subTest(body=body):
                g,t,c=gateway([(200,{},body)])
                with self.assertRaises(p.ReviewNeeded):g.article('https://example.org/mock/1')
                self.assertEqual(len(t.calls),1)
    def test_longest_allow_rule_and_wildcard(self):
        for rules in [b'User-agent: *\nDisallow: /\nAllow: /mock/',b'User-agent: *\nDisallow: /*\nAllow: /mock/*$']:
            g,t,c=gateway([(200,{},rules),(200,{'content-type':'text/html'},HTML)])
            self.assertEqual(g.article('https://example.org/mock/1').status,200)
    def test_matching_user_agent_group_is_most_specific(self):
        g,t,c=gateway([(200,{},b'User-agent: *\nDisallow: /\n\nUser-agent: HormuzResearchPilot\nAllow: /\n'),(200,{'content-type':'text/html'},HTML)])
        self.assertEqual(g.article('https://example.org/mock/1').status,200)
    def test_crawl_delay_decimal_and_request_rate(self):
        g,t,c=gateway([(200,{},b'User-agent: *\nCrawl-delay: 2.5\nRequest-rate: 1/7'),(200,{'content-type':'text/html'},HTML)])
        g.article('https://example.org/mock/1');self.assertGreaterEqual(c.t,1007)
    def test_robots_statuses_and_empty(self):
        for code in [404,410]:
            g,t,c=gateway([(code,{},b''),(200,{'content-type':'text/html'},HTML)])
            self.assertEqual(g.article('https://example.org/mock/1').status,200)
        for code,exc in [(401,p.PolicyBlocked),(403,p.PolicyBlocked),(429,p.RetryLater),(503,p.RetryLater)]:
            g,t,c=gateway([(code,{},b'')])
            with self.assertRaises(exc):g.article('https://example.org/mock/1')
            self.assertEqual(len(t.calls),1)
        g,t,c=gateway([(200,{},b' # intentionally empty\n'),(200,{'content-type':'text/html'},HTML)])
        self.assertEqual(g.article('https://example.org/mock/1').status,200)
    def test_large_robots_and_cross_origin_redirect(self):
        for response in [(200,{},b'x'*(p.default_config()['max_robots_bytes']+1)),(302,{'location':'https://other.example/robots.txt'},b'')]:
            g,t,c=gateway([response])
            with self.assertRaises(p.ReviewNeeded):g.article('https://example.org/mock/1')
            self.assertEqual(len(t.calls),1)
    def test_forbidden_domain_not_retried(self):
        g,t,c=gateway([(404,{},b''),(403,{},b'')])
        for _ in range(2):
            with self.assertRaises(p.PolicyBlocked):g.article('https://example.org/mock/1')
        self.assertEqual(len(t.calls),2)
    def test_retry_after_HTTP_date_and_persistent_cooldown(self):
        from email.utils import formatdate
        g,t,c=gateway([(404,{},b''),(429,{'retry-after':formatdate(1120,usegmt=True)},b'')])
        with self.assertRaises(p.RetryLater) as e:g.article('https://example.org/mock/1')
        self.assertGreaterEqual(e.exception.when,1120)
        with self.assertRaises(p.RetryLater):g.article('https://example.org/mock/2')
        self.assertEqual(len(t.calls),2)
    def test_same_origin_robots_redirect_and_escaped_path(self):
        g,t,c=gateway([(302,{'location':'/rules.txt'},b''),(200,{},b'User-agent: *\nDisallow: /m%6fck/private\nAllow: /mock/'),(200,{'content-type':'text/html'},HTML)])
        self.assertEqual(g.article('https://example.org/mock/1').status,200)
        self.assertEqual(t.calls[1],'https://example.org/rules.txt')
    def test_429_cooldown_survives_new_gateway(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            g,t,c=gateway([(429,{'retry-after':'60'},b'')]);path=Path(tmp)/'network_policy_state.json';g.bind_state(path)
            with self.assertRaises(p.RetryLater):g.one('https://example.org/robots.txt',100,'robots')
            g2,t2,c2=gateway([]);g2.bind_state(path)
            with self.assertRaises(p.RetryLater):g2.article('https://example.org/mock/1')
            self.assertEqual(t2.calls,[])

class OutcomeRegression(unittest.TestCase):
    setUp=fixtures.StateTests.setUp
    tearDown=fixtures.StateTests.tearDown
    def run_page(self,body='Short synthetic Strait of Hormuz.',language='en',prob=.99,items=None):
        g,t,c=gateway(items or [(404,{},b''),(200,{'content-type':'text/html'},HTML)])
        def extracted(h,u,title,config):
            def backend(h,u,k):return dict(body=body,title=title)
            r=p.extract_html(h,u,title,config,backend=backend)
            r['quality'].update(language=language,language_probability=prob,is_English_body='unknown' if language=='unknown' else '1' if language=='en' else '0')
            if language!='en':r['quality']['review_reasons'].append('language_review')
            return r
        e=p.Engine(self.out,self.rows,p.default_config(),g,store=self.store,extractor=extracted,now=c.now)
        e.step(self.rows[0]['article_id']);return e,g,t,c
    def result(self,index=0):return p.jread(self.out/'results'/f'{self.rows[index]["article_id"]}.json')
    def test_request_started_recorded_even_connection_error(self):
        g,t,c=gateway([(404,{},b'')])
        original=t.get
        def broken(u,n):
            if u.endswith('robots.txt'):return original(u,n)
            raise TimeoutError('synthetic connection timeout')
        t.get=broken
        p.Engine(self.out,self.rows,p.default_config(),g,store=self.store,now=c.now).step(self.rows[0]['article_id'])
        events=g.request_log
        self.assertEqual(sum(x.get('phase')=='started' and x['kind']=='article' for x in events),1)
        self.assertTrue(any(x.get('error_type')=='TimeoutError' for x in events))
        self.assertEqual(p.summarize_run(self.out,self.rows)['metrics']['article_HTTP_started_URLs']['count'],1)
    def test_DNS_failure_not_HTTP_request(self):
        g,t,c=gateway([])
        def fail(h,port):raise socket.gaierror('synthetic DNS failure')
        g.guard=p.URLGuard(resolver=fail)
        p.Engine(self.out,self.rows,p.default_config(),g,store=self.store,now=c.now).step(self.rows[0]['article_id'])
        self.assertEqual(t.calls,[]);self.assertEqual(self.store.load()[0]['status'],'error')
        self.assertEqual(p.summarize_run(self.out,self.rows)['metrics']['article_HTTP_started_URLs']['count'],0)
    def test_private_DNS_no_transport(self):
        g,t,c=gateway([]);g.guard=p.URLGuard(resolver=lambda h,port:['10.0.0.1'])
        p.Engine(self.out,self.rows,p.default_config(),g,store=self.store,now=c.now).step(self.rows[0]['article_id'])
        self.assertEqual(t.calls,[]);self.assertEqual(self.store.load()[0]['status'],'policy_blocked')
    def test_HTML_success_empty_extraction_separated(self):
        self.run_page(body='',language='unknown',prob=None)
        r=self.result();self.assertTrue(r['html_saved']);self.assertFalse(r['body_extraction_success'])
        s=p.summarize_run(self.out,self.rows)['metrics']
        self.assertEqual(s['HTTP200_HTML_saved_URLs']['count'],1);self.assertEqual(s['nonempty_body_URLs']['count'],0)
        self.assertEqual(s['nonempty_body_URLs']['denominator'],1)
    def test_unknown_language_retains_body(self):
        self.run_page(language='unknown',prob=None)
        r=self.result();self.assertTrue(r['body_extraction_success']);self.assertEqual(r['outcome']['English_auto'],'unknown')
        s=p.summarize_run(self.out,self.rows)['metrics'];self.assertEqual(s['English_auto_bodies']['count'],0);self.assertEqual(s['nonempty_body_URLs']['count'],1)
    def test_English_no_keyword_and_short_body_retained(self):
        self.run_page(body='A brief synthetic report about commercial shipping.')
        r=self.result();self.assertTrue(r['body_extraction_success']);self.assertFalse(r['outcome']['body_keyword_present'])
        self.assertTrue(r['outcome']['short_body_suspected']);self.assertTrue(r['outcome']['human_review_required'])
        self.assertTrue((self.out/r['chosen_text_path']).exists())
    def test_summary_separates_policy_error_unattempted(self):
        g,t,c=gateway([]);g.policies={}
        p.Engine(self.out,self.rows,p.default_config(),g,store=self.store,now=c.now).step(self.rows[0]['article_id'])
        s=p.summarize_run(self.out,self.rows)['metrics']
        self.assertEqual(s['processing_attempted_URLs']['count'],1);self.assertEqual(s['article_HTTP_started_URLs']['count'],0)
        self.assertEqual(s['human_review_URLs']['count'],1);self.assertEqual(s['unattempted_URLs']['count'],199)
    def test_retry_limit_5xx_and_due_time(self):
        g,t,c=gateway([(404,{},b''),(503,{},b''),(503,{},b''),(503,{},b'')])
        e=p.Engine(self.out,self.rows,p.default_config(),g,store=self.store,now=c.now);aid=self.rows[0]['article_id']
        e.step(aid);e.step(aid);self.assertEqual(len(t.calls),2)
        c.t+=10;e.step(aid);c.t+=10;e.step(aid);c.t+=1000;e.step(aid)
        self.assertEqual(self.store.load()[0]['attempts'],'3');self.assertEqual(len(t.calls),4)
        self.assertEqual(p.summarize_run(self.out,self.rows)['HTTP_request_counts']['article_started'],3)
    def test_saved_HTML_resume_and_no_duplicate_request(self):
        e,g,t,c=self.run_page();aid=self.rows[0]['article_id'];rows=self.store.load();rows[0]['status']='running';self.store.save(rows)
        self.store.recover_running();e.step(aid);self.assertEqual(len(t.calls),2)
        # Interrupted after HTML/provenance but before extraction/result.
        (self.out/'results'/f'{aid}.json').unlink();rows=self.store.load();rows[0]['status']='running';self.store.save(rows)
        self.store.recover_running();e.step(aid);self.assertEqual(len(t.calls),2)
        self.assertTrue(self.result()['body_extraction_success'])
    def test_journal_survives_new_gateway_and_summary(self):
        e,g,t,c=self.run_page();g2,t2,c2=gateway([]);g2.bind_state(self.out/'network_policy_state.json')
        self.assertEqual(len(g2.request_log),len(g.request_log))
        self.assertEqual(p.summarize_run(self.out,self.rows)['HTTP_request_counts']['robots_started'],1)
    def test_duplicate_body_summary_preserves_both(self):
        e,g,t,c=self.run_page(body='Synthetic ships cross the Strait of Hormuz. '*40)
        t.items.append((200,{'content-type':'text/html'},HTML));e.step(self.rows[1]['article_id'])
        s=p.summarize_run(self.out,self.rows)['metrics']
        self.assertEqual(s['nonempty_body_URLs']['count'],2);self.assertEqual(s['duplicate_suspected_URLs']['count'],2)
    def test_HTTP200_invalid_HTML_recorded_without_HTML_saved(self):
        self.run_page(items=[(404,{},b''),(200,{'content-type':'application/pdf'},b'PDF')])
        r=self.result();self.assertTrue(r['http_access_success']);self.assertFalse(r['html_saved'])
    def test_projection_has_all_200_rows_unknown_not_false(self):
        rows=p.article_outcomes(self.out,self.rows);self.assertEqual(len(rows),200)
        self.assertEqual(rows[0]['English_auto'],'unknown');self.assertEqual(rows[0]['automated_quality_pass'],'unknown')
    def test_nonEnglish_body_is_retained_without_English_count(self):
        self.run_page(language='fr',prob=.99)
        self.assertTrue(self.result()['body_extraction_success'])
        self.assertEqual(p.summarize_run(self.out,self.rows)['metrics']['English_auto_bodies']['count'],0)
    def test_extractor_exception_preserves_HTTP_and_HTML(self):
        g,t,c=gateway([(404,{},b''),(200,{'content-type':'text/html'},HTML)])
        def fail(*args):raise RuntimeError('synthetic extractor crash')
        p.Engine(self.out,self.rows,p.default_config(),g,store=self.store,now=c.now,extractor=fail).step(self.rows[0]['article_id'])
        r=self.result();self.assertTrue(r['html_saved']);self.assertTrue(r['http_access_success']);self.assertFalse(r['body_extraction_success'])
    def test_automatic_quality_pass_is_not_human_final(self):
        e,g,t,c=self.run_page(body='Synthetic shipping traverses the Strait of Hormuz. '*40)
        r=self.result();r['quality']['review_reasons']=[];r['quality'].update(language='en',language_probability=.99)
        p.atomic_json(self.out/'results'/f'{self.rows[0]["article_id"]}.json',r)
        row=p.article_outcomes(self.out,self.rows)[0]
        self.assertTrue(row['automated_quality_pass']);self.assertTrue(row['human_review_required']);self.assertEqual(row['human_final_label'],'')
    def test_near_duplicate_group_flags_both_articles(self):
        e,g,t,c=self.run_page(body='Synthetic shipping traverses the Strait of Hormuz. '*40)
        r=self.result();r['quality'].update(duplicate_body_ids=[],near_duplicate_body_ids=[self.rows[1]['article_id']])
        p.atomic_json(self.out/'results'/f'{self.rows[0]["article_id"]}.json',r)
        outcomes=p.article_outcomes(self.out,self.rows)
        # A pending URL with no body must not be counted as a duplicate body.
        self.assertTrue(outcomes[0]['duplicate_suspected']);self.assertFalse(outcomes[1]['duplicate_suspected'])
    def test_default_dryrun_no_network_and_no_state_write(self):
        import io,contextlib,sys
        root=self.out/'root';run=root/'hormuz_2026_data/runs/body_pilot_200_mock';run.mkdir(parents=True)
        st=p.StateStore(run,self.rows);st.initialize();before=p.sha(st.path)
        with patch.object(p,'ROOT',root),patch.object(p,'load_locked_pilot',return_value=(self.rows,p.default_config())),patch.object(sys,'argv',['body_pilot_200.py','--run-dir',str(run)]),patch('socket.getaddrinfo',side_effect=AssertionError('DNS forbidden')),patch.object(p.NativeTransport,'get_validated',side_effect=AssertionError('transport forbidden')):
            buf=io.StringIO()
            with contextlib.redirect_stdout(buf):p.main()
        self.assertEqual(json.loads(buf.getvalue())['HTTP_requests'],0);self.assertEqual(p.sha(st.path),before)
    def test_retry_ceiling_even_if_stale_next_retry_present(self):
        states=self.store.load();states[0].update(status='error',attempts='3',next_retry_at='1');self.store.save(states)
        g,t,c=gateway([]);p.Engine(self.out,self.rows,p.default_config(),g,store=self.store,now=c.now).step(self.rows[0]['article_id'])
        self.assertEqual(t.calls,[])
    def test_whitespace_only_extraction_is_not_nonempty_body(self):
        self.run_page(body=' \n\t ',language='unknown',prob=None)
        self.assertFalse(self.result()['body_extraction_success'])
        self.assertEqual(p.summarize_run(self.out,self.rows)['metrics']['nonempty_body_URLs']['count'],0)
    def test_lock_rejects_changed_input_without_state_reinitialization(self):
        p.csvwrite(self.out/'pilot_200_queue.csv',self.rows);p.atomic_json(self.out/'pilot_config.json',p.default_config())
        p.atomic_json(self.out/'pilot_lock.json',dict(immutable_hashes={n:p.sha(self.out/n) for n in ['pilot_200_queue.csv','pilot_config.json']},code_hashes={'scripts/body_pilot_200.py':p.sha(p.ROOT/'scripts/body_pilot_200.py')}))
        p.load_locked_pilot(self.out);before=p.sha(self.store.path)
        (self.out/'pilot_config.json').write_text('{}',encoding='utf-8')
        with self.assertRaises(p.PolicyBlocked):p.load_locked_pilot(self.out)
        self.assertEqual(p.sha(self.store.path),before)
    def test_finalization_failure_restores_exact_original_lock_bytes(self):
        import verify_body_pilot_200_regression as h
        self.assertTrue(hasattr(h,'_finalize'))
        lock=self.out/'pilot_lock.json';original=b'{"old": "exact bytes"}\n';lock.write_bytes(original)
        def interrupted():
            lock.write_bytes(b'{"changed":true}')
            raise RuntimeError('synthetic post-lock report write failure')
        with patch.object(h,'RUN',self.out),patch.object(h,'_finalize',side_effect=interrupted),self.assertRaises(RuntimeError):h.finalize()
        self.assertEqual(lock.read_bytes(),original)
    def test_actual_simhash_near_duplicate_detection(self):
        base='Synthetic commercial shipping passes through the Strait of Hormuz. '*100
        e,g,t,c=self.run_page(body=base)
        def altered(h,u,title,config):return p.extract_html(h,u,title,config,backend=lambda h,u,k:dict(body=base+' A minor artificial addition.',title=title))
        e.extractor=altered;t.items.append((200,{'content-type':'text/html'},HTML));e.step(self.rows[1]['article_id'])
        self.assertIn(self.rows[0]['article_id'],self.result(1)['quality']['near_duplicate_body_ids'])
