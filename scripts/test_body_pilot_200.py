"""Offline tests: synthetic URLs/HTML only, never actual article sites."""
import unittest,tempfile,csv,json,hashlib,socket
from pathlib import Path
from unittest.mock import patch
from body_pilot_200 import (URLGuard,PolicyBlocked,ReviewNeeded,RetryLater,Response,
    Gateway,Engine,StateStore,keyword_metrics,quality_metrics,access_check,
    default_config,load_locked_pilot,extract_html,validate_approval,NativeTransport)

HTML=b'<html><head><title>Mock Strait of Hormuz report</title></head><body><article><h1>Mock Strait of Hormuz report</h1><p>This is a synthetic fixture about ships in the Strait of Hormuz. It is not a collected news article.</p></article></body></html>'
def queue():
    return [dict(article_id=f'{i:016x}',pilot_order=str(i+1),distributed_order=str(i+1),request_url=f'https://example.org/mock/{i}',domain='example.org',title='Mock Strait of Hormuz report',month='2026-01',title_dup_group_id='',title_dup_group_size='1') for i in range(200)]
class Clock:
    def __init__(self):self.t=1000.
    def now(self):return self.t
    def sleep(self,n):self.t+=n
class FakeTransport:
    def __init__(self,items):self.items=list(items);self.calls=[]
    def get(self,url,limit):
        self.calls.append(url)
        if not self.items:raise AssertionError('unexpected mock request: '+url)
        status,headers,body=self.items.pop(0)
        return Response(url,status,headers,body)
def approval(rows):return {r['domain']:dict(terms_reviewed=True,allowed=True,evidence='synthetic mock policy') for r in rows}
def gateway(items,clock=None):
    c=clock or Clock();t=FakeTransport(items)
    g=Gateway(default_config(),t,URLGuard(resolver=lambda h,p:['93.184.216.34']),approval(queue()),c.now,c.sleep)
    return g,t,c

class URLTests(unittest.TestCase):
    def test_static_private_and_credential_block(self):
        for u in ['file:///x','http://localhost/a','http://127.0.0.1/a','https://[::1]/a','https://u:p@example.org/a','http://example.org:22/a','http://example.org/\r\nX:x','https://example.org/a?token=secret']:
            with self.subTest(u=u),self.assertRaises(PolicyBlocked):URLGuard().check(u,resolve=False)
    def test_dns_public_only_and_mixed_answers_block(self):
        with self.assertRaises(PolicyBlocked):URLGuard(resolver=lambda h,p:['93.184.216.34','10.0.0.1']).check('https://example.org/a')
        self.assertEqual(URLGuard(resolver=lambda h,p:['93.184.216.34']).check('https://example.org/a')['ip'],'93.184.216.34')
    def test_numeric_local_alias_resolved_before_connect(self):
        with self.assertRaises(PolicyBlocked):URLGuard(resolver=lambda h,p:['127.0.0.1']).check('http://127.1/a')
    def test_no_DNS_on_static_preparation(self):
        with patch('socket.getaddrinfo',side_effect=AssertionError('DNS forbidden')):URLGuard().check('https://example.org/a',resolve=False)

class QualityTests(unittest.TestCase):
    def test_keyword_variations_and_standalone(self):
        x=keyword_metrics('STRAIT-of-Hormuz; Straits of Hormuz; Hormuz Strait; Hormuz','Strait of Hormuz')
        self.assertEqual(x['strait_of_hormuz_count'],1);self.assertEqual(x['straits_of_hormuz_count'],1)
        self.assertEqual(x['hormuz_strait_count'],1);self.assertEqual(x['hormuz_only_count'],1)
        self.assertEqual(x['body_relevance'],'explicit_strait_mention');self.assertTrue(x['title_keyword_present'])
        self.assertEqual(keyword_metrics('Hormuz; Strait of Hormuz')['hormuz_only_count'],1)
    def test_no_keyword_and_short_are_review_not_deleted(self):
        x=quality_metrics('This is a short synthetic English dispatch.','Dispatch',default_config())
        self.assertIn('short_body_review',x['review_reasons']);self.assertIn('no_body_keyword',x['review_reasons'])
        self.assertFalse(x['human_final_label']);self.assertEqual(x['body_relevance'],'no_body_keyword')
    def test_subscription_ad_is_not_paywall(self):
        self.assertEqual(access_check(b'<article>A public article.</article><footer>Subscribe to our newsletter</footer>'),'no_gate_detected_not_verified')
        self.assertEqual(access_check(b'<div id="paywall">Subscribe to continue reading</div>'),'paid_or_partial_gate_detected')
        self.assertEqual(access_check(b'<div id="login-wall">Sign in to continue reading</div>'),'login_gate_detected')
    def test_fallback_preserves_primary_not_longest(self):
        def fake(html,url,kind):return dict(body='short primary' if kind!='newsplease' else 'long fallback '*100,title='Mock')
        r=extract_html(HTML,'https://example.org/a','Mock',default_config(),backend=fake)
        self.assertEqual(r['chosen_extractor'],'trafilatura_default');self.assertEqual(r['body'],'short primary')
        self.assertIn('newsplease',r['extractors'])
    def test_empty_primary_uses_provisional_fallback_review(self):
        def fake(h,u,k):return dict(body='' if k!='newsplease' else 'A mock Hormuz report about shipping.',title='Mock')
        r=extract_html(HTML,'https://example.org/a','Mock',default_config(),backend=fake)
        self.assertEqual(r['chosen_extractor'],'newsplease');self.assertIn('fallback_used_review',r['quality']['review_reasons'])
    def test_installed_extractors_on_synthetic_HTML_only(self):
        from body_pilot_200 import extraction_backend
        paragraphs=[('This synthetic report describes commercial shipping through the Strait of Hormuz. The artificial example checks software behavior and does not describe an actual news event. ')*5,
                    ('Researchers use this mock article to check English text extraction, metadata alignment, and careful retention of source files. No website was contacted for these words. ')*5]
        markup=('<html><head><title>Mock Strait of Hormuz report</title></head><body><article><h1>Mock Strait of Hormuz report</h1>'+''.join('<p>'+p+'</p>' for p in paragraphs)+'</article></body></html>').encode()
        r=extract_html(markup,'https://example.org/mock','Mock Strait of Hormuz report',default_config())
        self.assertGreater(r['extractors']['trafilatura_default']['chars'],0)
        # Exercise actual news-please on the same synthetic HTML through the
        # normal fallback path, deliberately emptying the synthetic primary.
        def backend(h,u,k):
            if k!='newsplease':return dict(body='',title='Mock')
            try:return extraction_backend(h,u,k)
            except Exception:
                import traceback
                traceback.print_exc()
                raise
        with patch('tempfile.tempdir',None):
            r=extract_html(markup,'https://example.org/mock','Mock',default_config(),backend=backend)
        self.assertNotEqual(r['extractors']['newsplease']['status'],'extractor_error')

class PolicyTests(unittest.TestCase):
    def test_robots_disallow_prevents_article_request(self):
        g,t,c=gateway([(200,{'content-type':'text/plain'},b'User-agent: *\nDisallow: /')])
        with self.assertRaises(PolicyBlocked):g.article('https://example.org/mock/1')
        self.assertEqual(t.calls,['https://example.org/robots.txt'])
    def test_robots_403_is_not_missing_allow(self):
        g,t,c=gateway([(403,{},b'')])
        with self.assertRaises(PolicyBlocked):g.article('https://example.org/mock/1')
        self.assertEqual(len(t.calls),1)
    def test_unknown_terms_prevents_robots_too(self):
        g,t,c=gateway([]);g.policies={}
        with self.assertRaises(ReviewNeeded):g.article('https://example.org/mock/1')
        self.assertEqual(t.calls,[])
    def test_redirect_safety_and_downgrade(self):
        g,t,c=gateway([(404,{},b''),(302,{'location':'http://127.0.0.1/x'},b'')])
        with self.assertRaises(PolicyBlocked):g.article('https://example.org/mock/1')
        self.assertEqual(len(t.calls),2)
    def test_cross_host_redirect_needs_separate_terms(self):
        g,t,c=gateway([(404,{},b''),(302,{'location':'https://other.example/a'},b'')])
        with self.assertRaises(ReviewNeeded):g.article('https://example.org/mock/1')
        self.assertEqual(len(t.calls),2)
    def test_rate_limit_includes_robots(self):
        g,t,c=gateway([(404,{},b''),(200,{'content-type':'text/html'},HTML)])
        g.article('https://example.org/mock/1');self.assertGreaterEqual(c.t,1002.)
    def test_robots_request_rate_is_respected(self):
        g,t,c=gateway([(200,{},b'User-agent: *\nRequest-rate: 1/10\n'),(200,{'content-type':'text/html'},HTML)])
        g.article('https://example.org/mock/1');self.assertGreaterEqual(c.t,1010.)
    def test_repeated_domain_failures_open_circuit(self):
        g,t,c=gateway([])
        for _ in range(3):g.failure('example.org')
        with self.assertRaises(RetryLater):g.article('https://example.org/mock/1')
        self.assertEqual(t.calls,[])
    def test_429_sets_domain_cooldown(self):
        g,t,c=gateway([(404,{},b''),(429,{'retry-after':'60'},b'')])
        with self.assertRaises(RetryLater) as r:g.article('https://example.org/mock/1')
        self.assertGreaterEqual(r.exception.when,1060)
    def test_long_server_retry_after_never_shortened(self):
        for code in [429,503]:
            g,t,c=gateway([(404,{},b''),(code,{'retry-after':'86400'},b'')])
            with self.assertRaises(ReviewNeeded):g.article('https://example.org/mock/1')
            self.assertGreaterEqual(g.cooldown['example.org'],c.now()+86400)
    def test_absolute_response_deadline_interrupts_slow_headers(self):
        callbacks=[]
        class Timer:
            def __init__(self,delay,callback):callbacks.append(callback)
            def start(self):pass
            def cancel(self):pass
        class Conn:
            def __init__(self,*a,**kw):self.closed=False;self.sock=self
            def connect(self):pass
            def settimeout(self,t):pass
            def request(self,*a,**kw):pass
            def getresponse(self):
                callbacks[0]()
                raise OSError('closed by mock deadline')
            def shutdown(self,*a):self.closed=True
            def close(self):self.closed=True
        config=dict(default_config(),response_deadline_sec=.03)
        with patch('body_pilot_200.threading.Timer',Timer),patch('http.client.HTTPConnection',Conn),self.assertRaises(TimeoutError):
            NativeTransport(config).get_validated('http://example.org/a',100,dict(host='example.org',port=80,scheme='http',path='/a',ip='93.184.216.34'))
    def test_non_HTML_and_size_rejected(self):
        for headers,body in [({'content-type':'application/pdf'},b'PDF'),({'content-type':'text/html'},b'x'*(default_config()['max_html_bytes']+1))]:
            g,t,c=gateway([(404,{},b''),(200,headers,body)])
            with self.assertRaises(ReviewNeeded):g.article('https://example.org/mock/1')

class StateTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.out=Path(self.temp.name);self.rows=queue();self.store=StateStore(self.out,self.rows);self.store.initialize()
    def tearDown(self):self.temp.cleanup()
    def test_exact_200_and_unique_guard(self):
        with self.assertRaises(ValueError):StateStore(self.out,self.rows[:199])
        bad=queue();bad[-1]=bad[0]
        with self.assertRaises(ValueError):StateStore(self.out,bad)
    def test_terminal_rows_not_re_requested(self):
        rows=self.store.load();rows[0]['status']='done';self.store.save(rows)
        class Never:
            def article(self,u):raise AssertionError('should not request')
        Engine(self.out,self.rows,default_config(),Never(),store=self.store).step(self.rows[0]['article_id'])
        self.assertEqual(self.store.load()[0]['status'],'done')
    def test_interrupted_without_HTML_is_review_not_refetch(self):
        rows=self.store.load();rows[0]['status']='running';self.store.save(rows)
        self.store.recover_running();r=self.store.load()[0]
        self.assertEqual(r['status'],'review');self.assertIn('interrupted',r['error_type'])
    def test_orphan_HTML_does_not_trigger_new_request(self):
        p=self.out/'html'/f'{self.rows[0]["article_id"]}.html';p.parent.mkdir();p.write_bytes(HTML)
        class Never:
            def article(self,u):raise AssertionError('must not refetch orphan')
        Engine(self.out,self.rows,default_config(),Never(),store=self.store).step(self.rows[0]['article_id'])
        self.assertEqual(self.store.load()[0]['status'],'review')
    def test_retry_cap_and_resume_timestamp(self):
        c=Clock()
        class Fail:
            def article(self,u):raise RetryLater('mock_503',c.now()+2)
        e=Engine(self.out,self.rows,dict(default_config(),max_retries=1),Fail(),store=self.store,now=c.now)
        e.step(self.rows[0]['article_id']);r=self.store.load()[0];self.assertEqual(r['status'],'error');self.assertEqual(r['attempts'],'1')
        e.step(self.rows[0]['article_id']);self.assertEqual(self.store.load()[0]['attempts'],'1')
        c.t+=3;e.step(self.rows[0]['article_id']);r=self.store.load()[0];self.assertEqual(r['attempts'],'2');self.assertEqual(r['next_retry_at'],'')
        result=json.loads((self.out/r['result_path']).read_text());self.assertEqual(result['attempts'],'2')
    def test_old_failed_result_cannot_recover_later_inflight_request(self):
        rows=self.store.load();rows[0].update(status='running',attempts='2');self.store.save(rows)
        p=self.out/'results'/f'{rows[0]["article_id"]}.json';p.parent.mkdir();p.write_text(json.dumps(dict(article_id=rows[0]['article_id'],request_url=rows[0]['URL'],state='error',attempts='1')))
        self.store.recover_running();self.assertEqual(self.store.load()[0]['status'],'review')
    def test_fixed_allowlist_rejects_extra_article(self):
        with self.assertRaises(ValueError):Engine(self.out,self.rows,default_config(),None).step('ffffffffffffffff')
    def test_mock_pipeline_records_without_corpus_claim(self):
        class Page:
            def article(self,u):return Response(u,200,{'content-type':'text/html'},HTML)
        def fake(h,u,k):return dict(body=('Mock ships cross the Strait of Hormuz. '*60),title='Mock Strait of Hormuz report')
        e=Engine(self.out,self.rows,default_config(),Page(),store=self.store,extractor=lambda h,u,t,c:extract_html(h,u,t,c,backend=fake))
        e.step(self.rows[0]['article_id']);self.assertIn(self.store.load()[0]['status'],['done','review'])
        e.step(self.rows[1]['article_id']);result=json.loads((self.out/'results'/f'{self.rows[1]["article_id"]}.json').read_text())
        self.assertIn(self.rows[0]['article_id'],result['quality']['duplicate_body_ids']);self.assertFalse(result['quality']['human_final_label'])
    def test_gate_saves_HTML_but_skips_extract(self):
        class Page:
            def article(self,u):return Response(u,200,{'content-type':'text/html'},b'<div id="paywall">Subscribe to continue reading</div>')
        def forbidden(*a):raise AssertionError('do not extract paid gate')
        Engine(self.out,self.rows,default_config(),Page(),store=self.store,extractor=forbidden).step(self.rows[0]['article_id'])
        self.assertEqual(self.store.load()[0]['status'],'policy_blocked')
    def test_execution_requires_hash_bound_separate_approval(self):
        with self.assertRaises(PolicyBlocked):validate_approval({},'abc',self.rows)

if __name__=='__main__':
    with patch('socket.getaddrinfo',side_effect=AssertionError('offline tests: DNS forbidden')),patch('socket.create_connection',side_effect=AssertionError('offline tests: network forbidden')):
        unittest.main()
