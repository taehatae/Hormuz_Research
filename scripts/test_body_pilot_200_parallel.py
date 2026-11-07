"""Small offline regression suite for shared scheduling and recovery."""
import concurrent.futures
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import body_pilot_200 as p
from test_body_pilot_200 import queue, Clock, HTML, approval

p._OFFLINE_PROCESS=True

class ParallelTests(unittest.TestCase):
    def setup_run(self, different=False):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.out=Path(self.tmp.name);self.rows=queue()
        if different:
            for i,r in enumerate(self.rows):
                r['domain']=f'host{i}.example.org';r['request_url']=f'https://{r["domain"]}/mock/{i}'
        self.store=p.StateStore(self.out,self.rows);self.store.initialize()
        states=self.store.load()
        for s in states[4:]:s['status']='review'
        self.store.save(states)
        self.clock=Clock();self.manager=threading.get_ident();self.writes=[]
        save=self.store.save
        def tracked(rows):self.writes.append(threading.get_ident());return save(rows)
        self.store.save=tracked

    def engine(self,transport):
        g=p.Gateway(p.default_config(),transport,p.URLGuard(resolver=lambda h,port:['93.184.216.34']),approval(self.rows),self.clock.now,self.clock.sleep)
        # Synthetic gate keeps extractor dependencies outside scheduling tests.
        return p.Engine(self.out,self.rows,p.default_config(),g,self.store,now=self.clock.now)

    def test_cross_domain_parallel_and_single_writer(self):
        self.setup_run(True);barrier=threading.Barrier(4);calls=[]
        class Transport:
            def get(_,url,limit):
                calls.append(url)
                if url.endswith('/robots.txt'):return p.Response(url,404,{},b'')
                barrier.wait(timeout=5)
                return p.Response(url,200,{'content-type':'text/html'},HTML)
        engine=self.engine(Transport());disk_threads=[];write=p.atomic_json
        def tracked(*args,**kwargs):disk_threads.append(threading.get_ident());return write(*args,**kwargs)
        with patch.object(p,'access_check',return_value='gate_text_signal_review'),patch.object(p,'atomic_json',side_effect=tracked):
            engine.run_parallel(progress=lambda x:None)
        self.assertEqual(len(calls),8);self.assertEqual(set(self.writes+disk_threads),{self.manager})
        events=engine.gateway.request_log
        starts=[e for e in events if e['phase']=='started']
        self.assertEqual(len({e['request_id'] for e in starts}),8)
        for r in self.rows[:4]:
            result=p.jread(self.out/'results'/f'{r["article_id"]}.json')
            self.assertTrue(result['html_saved'])
            self.assertTrue(all(e['article_id']==r['article_id'] for e in result['HTTP_response_activity']))
        engine.run_parallel(progress=lambda x:None);self.assertEqual(len(calls),8)

    def test_shared_domain_interval_and_robots_cache(self):
        self.setup_run();calls=[];active=0;peak=0;lock=threading.Lock()
        class Transport:
            def get(_,url,limit):
                nonlocal active,peak
                with lock:active+=1;peak=max(peak,active);calls.append((url,self.clock.now()))
                try:return p.Response(url,200,{'content-type':'text/html'},b'User-agent: *\nCrawl-delay: 7\n' if url.endswith('/robots.txt') else HTML)
                finally:
                    with lock:active-=1
        engine=self.engine(Transport())
        record=engine.gateway.record;lagged=False
        def delayed_record(**fields):
            nonlocal lagged
            record(**fields)
            if fields.get('kind')=='article' and fields.get('phase')=='started' and not lagged:
                lagged=True;self.clock.sleep(100)  # manager stall, no real sleep
        engine.gateway.record=delayed_record
        with patch.object(p,'access_check',return_value='gate_text_signal_review'):engine.run_parallel(progress=lambda x:None)
        self.assertEqual(peak,1);self.assertEqual(sum(u.endswith('/robots.txt') for u,t in calls),1)
        self.assertTrue(all(b[1]-a[1]>=7 for a,b in zip(calls,calls[1:])))

    def test_interruption_drains_inflight_and_preserves_pending(self):
        self.setup_run(True);calls=[]
        class Transport:
            def get(_,url,limit):
                calls.append(url);return p.Response(url,404,{},b'') if url.endswith('/robots.txt') else p.Response(url,200,{'content-type':'text/html'},HTML)
        engine=self.engine(Transport());updates=[]
        def stop(update):
            updates.append(update)
            if len(updates)==2:raise KeyboardInterrupt
        with patch.object(p,'access_check',return_value='gate_text_signal_review'):
            with self.assertRaises(KeyboardInterrupt):engine.run_parallel(workers=2,progress=stop)
            states=self.store.load();self.assertFalse(any(s['status']=='running' for s in states))
            self.assertTrue(any(s['status']=='pending' for s in states[:4]))
            engine.run_parallel(workers=2,progress=lambda x:None)
        articles=[u for u in calls if not u.endswith('/robots.txt')]
        self.assertEqual(len(articles),4);self.assertEqual(len(set(articles)),4)

    def test_unreviewed_policy_no_dns_and_invalid_workers(self):
        self.setup_run();engine=self.engine(None);engine.gateway.policies={}
        with patch.object(engine.gateway.guard,'resolver',side_effect=AssertionError('DNS forbidden')):
            engine.run_parallel(progress=lambda x:None)
        self.assertFalse(engine.gateway.request_log)
        with self.assertRaises(ValueError):engine.run_parallel(workers=0)

    def test_redirect_target_shares_domain_lock(self):
        self.setup_run(True);calls=[];starts=threading.Barrier(4)
        class Transport:
            def get(_,url,limit):
                calls.append((url,self.clock.now()))
                if url.endswith('/robots.txt'):return p.Response(url,404,{},b'')
                if 'target.example.org' not in url:
                    starts.wait(timeout=5)
                    return p.Response(url,302,{'location':'https://target.example.org/'+url.rsplit('/',1)[-1]},b'')
                return p.Response(url,200,{'content-type':'text/html'},HTML)
        engine=self.engine(Transport());engine.gateway.policies['target.example.org']=dict(terms_reviewed=True,allowed=True,evidence='mock')
        with patch.object(p,'access_check',return_value='gate_text_signal_review'):engine.run_parallel(progress=lambda x:None)
        target=[(u,t) for u,t in calls if 'target.example.org' in u]
        self.assertEqual(len(target),5)
        self.assertTrue(all(b[1]-a[1]>=2 for a,b in zip(target,target[1:])))

    def test_saved_html_recovery_and_body_count(self):
        self.setup_run();aid=self.rows[0]['article_id'];hp=self.out/'html'/f'{aid}.html'
        hp.parent.mkdir();hp.write_bytes(HTML)
        p.atomic_json(hp.with_suffix('.json'),dict(article_id=aid,request_url=self.rows[0]['request_url'],final_url=self.rows[0]['request_url'],http_status=200,html_sha256=p.sha(hp)))
        states=self.store.load()
        states[0].update(status='running',attempts='1')
        for s in states[1:]:s['status']='review'
        self.store.save(states);self.store.recover_running()
        engine=self.engine(None);body='Synthetic Strait of Hormuz article body.'
        def extract(*args):
            return dict(body=body,chosen_extractor='trafilatura_default',quality=p.quality_metrics(body,'Mock',p.default_config()),extractors={'trafilatura_default':dict(body=body)})
        engine.extractor=extract
        with patch.object(p,'access_check',return_value='no_gate_detected_not_verified'):engine.run_parallel(progress=lambda x:None)
        self.assertFalse(engine.gateway.request_log);self.assertEqual(engine.progress()['body_acquired'],1)
        self.assertEqual(self.store.load()[0]['attempts'],'1')
        engine.run_parallel(progress=lambda x:None);self.assertEqual(engine.progress()['body_acquired'],1)

if __name__=='__main__':unittest.main()
