"""Policy-only collector safety tests. No real DNS/HTTP, no run artifacts."""
import unittest,tempfile
from pathlib import Path
from unittest.mock import patch
import review_pilot_access_policies as a
import body_pilot_200 as p
class PolicyOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.run=Path(self.tmp.name)
        p.atomic_json(self.run/'pilot_config.json',p.default_config())
        self.patch=patch.object(a,'RUN',self.run);self.patch.start()
        self.rows=[dict(article_id='0000000000000001',request_url='https://example.org/news/item',domain='example.org')]
        self.plan=dict(domains=[dict(hostname='example.org',terms_candidate='https://example.org/terms/')])
        self.c=a.Collector(self.plan,self.rows)
    def tearDown(self):self.patch.stop();self.tmp.cleanup()
    def test_article_and_home_and_unknown_host_forbidden(self):
        for url in ['https://example.org/news/item','https://example.org/','https://other.org/terms/','http://127.0.0.1/robots.txt','https://example.org/news/copyright-dispute/']:
            with self.subTest(url=url),self.assertRaises((p.ReviewNeeded,p.PolicyBlocked)):self.c.permitted(url,'policy','example.org')
    def test_dns_mixed_answers_no_transport(self):
        self.c.guard=p.URLGuard(resolver=lambda h,port:['93.184.216.34','127.0.0.1'])
        with patch.object(self.c.transport,'get_validated',side_effect=AssertionError('no transport')),self.assertRaises(p.PolicyBlocked):self.c.get('https://example.org/robots.txt','robots','example.org')
    def test_robot_cross_domain_and_article_redirect_not_followed(self):
        self.c.guard=p.URLGuard(resolver=lambda h,port:['93.184.216.34'])
        for kind,location in [('robots','https://www.example.org/robots.txt'),('policy','https://example.org/news/item')]:
            self.c.seen=set();self.c.last={}
            with patch.object(self.c.transport,'get_validated',return_value=p.Response('',302,{'location':location},b'')) as t,self.assertRaises((p.ReviewNeeded,p.PolicyBlocked)):
                self.c.get('https://example.org/robots.txt' if kind=='robots' else 'https://example.org/terms/','robots' if kind=='robots' else 'policy','example.org')
            self.assertEqual(t.call_count,1)
    def test_no_refetch_or_retry_after_forbidden(self):
        self.c.guard=p.URLGuard(resolver=lambda h,port:['93.184.216.34'])
        with patch.object(self.c.transport,'get_validated',return_value=p.Response('',403,{},b'')) as t:
            self.c.get('https://example.org/robots.txt','robots','example.org')
            with self.assertRaises(p.ReviewNeeded):self.c.get('https://example.org/terms/','policy','example.org')
            self.assertEqual(t.call_count,1)
    def test_policy_identity_and_short_excerpt(self):
        r=a.inspect_policy(b'<html><title>Terms of Use</title><h1>Terms</h1><p>You may not use automated robots or scrapers to collect articles.</p></html>','https://example.org/terms/')
        self.assertTrue(r['policy_document_confirmed']);self.assertLessEqual(len(r['automation_excerpt_20_words'].split()),20)
        self.assertFalse(a.inspect_policy(b'<title>404 Not Found</title>','https://example.org/terms/')['policy_document_confirmed'])
    def test_midterms_and_policy_words_inside_news_slug_are_not_policies(self):
        for url in ['https://example.org/world/46-politicians-after-midterms/','https://example.org/world/terms-of-trade-debate/','https://example.org/opinion/copyright-policy-debate/']:
            with self.subTest(url=url),self.assertRaises((p.ReviewNeeded,p.PolicyBlocked)):self.c.permitted(url,'policy','example.org')
        self.assertFalse(a.inspect_policy(b'<title>Election after midterms</title><h1>Election after midterms</h1>','https://example.org/world/midterms/')['policy_document_confirmed'])
    def test_no_permission_inferred_from_robots_allow_or_404(self):
        import finalize_pilot_access_review as f
        for status in ['parsed','no_directives']:
            d=dict(hostname='example.org',robots_status=status,robots_paths={'id':'allowed'},terms_document='')
            self.assertEqual(f.classify(d,{})[0],'review')
    def test_disallowed_test_paths_are_blocked_without_global_legal_claim(self):
        import finalize_pilot_access_review as f
        d=dict(hostname='example.org',robots_status='parsed',robots_paths={'id':'disallowed'},terms_document='')
        status,permission,reason=f.classify(d,{})
        self.assertEqual(status,'blocked');self.assertEqual(permission,'unknown')
    def test_allowed_requires_confirmed_same_document_and_met_conditions(self):
        import finalize_pilot_access_review as f
        d=dict(hostname='example.org',robots_status='parsed',robots_paths={'id':'allowed'},terms_document='https://example.org/terms/',policy_document_confirmed=True)
        m={'example.org':dict(automated_collection_permission='allowed',evidence_URL=d['terms_document'],reason='synthetic explicit policy',conditions_met=False)}
        with self.assertRaises(ValueError):f.classify(d,m)
        m['example.org']['conditions_met']=True;self.assertEqual(f.classify(d,m)[0],'allowed')
        m['example.org']['evidence_URL']='https://other.org/terms/'
        with self.assertRaises(ValueError):f.classify(d,m)
    def test_positive_terms_and_robot_disallow_conflict_remains_review(self):
        import finalize_pilot_access_review as f
        d=dict(hostname='example.org',robots_status='parsed',robots_paths={'id':'disallowed'},terms_document='https://example.org/terms/',policy_document_confirmed=True)
        m={'example.org':dict(automated_collection_permission='allowed',evidence_URL=d['terms_document'],reason='synthetic explicit policy',conditions_met=True)}
        self.assertEqual(f.classify(d,m)[0],'review')
    def test_http_policy_is_not_requested(self):
        with self.assertRaises(p.PolicyBlocked):self.c.permitted('http://example.org/terms/','policy','example.org')
    def test_policy_query_must_be_explicitly_planned(self):
        for query in ['p=123','url=/news/item','redirect=/world/item']:
            with self.subTest(query=query),self.assertRaises(p.ReviewNeeded):self.c.permitted('https://example.org/terms/?'+query,'policy','example.org')
if __name__=='__main__':
    p._OFFLINE_PROCESS=True
    unittest.main()
