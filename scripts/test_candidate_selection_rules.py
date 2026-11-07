import unittest,io
import pandas as pd
from candidate_selection_rules import classify,schedule

def frame(rows):
    base=dict(article_id='0123456789abcdef',url='https://example.org/news/a',title='Strait of Hormuz shipping update',nc_description='',nc_content_snippet='',nc_paid_content='False',nc_language='en',published_date='2026-03-01 00:00:00',domain='example.org',technical_risk='',source_review_hint='source_role_unverified')
    return pd.DataFrame([{**base,**r} for r in rows])

class Rules(unittest.TestCase):
    def test_topic_zero_unresolved_not_excluded(self):
        self.assertEqual(classify(frame([dict(title='Oil market update')])).iloc[0].selection_status,'review')
    def test_hormuz_name_needs_context(self):
        self.assertEqual(classify(frame([dict(title='Hormuz opens art exhibition')])).iloc[0].selection_status,'review')
    def test_live_fire_is_not_live_path(self):
        r=classify(frame([dict(url='https://example.org/news/hormuz-live-fire-drill')])).iloc[0]
        self.assertEqual(r.selection_status,'ready');self.assertEqual(r.type_hint,'no_nonprose_path_hint')
        self.assertEqual(classify(frame([dict(url='https://example.org/live/a')])).iloc[0].selection_status,'review')
    def test_paid_and_unknown_are_distinct(self):
        x=classify(frame([dict(nc_paid_content='True'),dict(nc_paid_content='')]))
        self.assertEqual(list(x.selection_status),['exclude','review'])
    def test_no_media_or_country_gate_and_NA_literal(self):
        d=pd.read_csv(io.StringIO('country\nNA\n'),dtype=str,keep_default_na=False)
        self.assertEqual(d.iloc[0,0],'NA')
        self.assertEqual(classify(frame([dict(domain='new.example',url='https://new.example/a')])).iloc[0].selection_status,'ready')
    def test_order_reproducible_and_no_quota_loss(self):
        d=pd.DataFrame([dict(article_id=f'{i:016x}',month='2026-01' if i<3 else '2026-08',domain=f'd{i%4}',priority=1) for i in range(30)])
        a,stats=schedule(d);b,_=schedule(d)
        self.assertEqual(list(a),list(b));self.assertEqual(set(a),set(d.index));self.assertEqual(stats['adjacent_same_domain'],0)
        self.assertEqual(set(d.loc[a[:4],'month']),{'2026-01','2026-08'})

if __name__=='__main__':unittest.main()
