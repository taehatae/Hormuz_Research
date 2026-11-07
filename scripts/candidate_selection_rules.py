"""Vectorized metadata rules and deterministic scheduling; no networking."""
import re
from collections import deque
import numpy as np
import pandas as pd

RULE_VERSION='distributed_metadata_v1'
PHRASE=re.compile(r'\b(?:strait\s+of\s+hormuz|hormuz\s+strait)\b',re.I)
HORMUZ=re.compile(r'\bhormuz\b',re.I)
MARITIME=re.compile(r'\b(?:strait|shipping|ship|ships|shipping|tanker|tankers|maritime|vessel|vessels|oil|crude|lng|naval|blockade|navigation|seaborne)\b',re.I)
CONTEXT=re.compile(r'\b(?:persian\s+gulf|gulf\s+of\s+oman|strait\s+of\s+ormuz|iran\w*.*(?:shipping|tanker|oil)|(?:shipping|tanker|oil).*iran\w*)\b',re.I)
TYPE_PATTERNS={
 'listing_path_hint':re.compile(r'/(?:tag|tags|category|author|search|keyword|keywords|topic)(?:/|$)',re.I),
 'video_path_hint':re.compile(r'/(?:video|videos|watch|videoshow)(?:/|$)',re.I),
 'infographic_path_hint':re.compile(r'/(?:infographic|infographics)(?:/|$)',re.I),
 'audio_path_hint':re.compile(r'/(?:listen|audio|podcast|podcasts)(?:/|$)',re.I),
 'live_path_hint':re.compile(r'/(?:live|liveblog|liveblogs|live-news|liveupdates)(?:/|$)',re.I)}

def classify(d):
    """Retain lexical inputs. No media whitelist, country gate or length threshold."""
    d=d.copy()
    text=d['title']+' '+d['nc_description']+' '+d['nc_content_snippet']
    phrase=text.str.contains(PHRASE,na=False)
    h=text.str.contains(HORMUZ,na=False);sea=text.str.contains(MARITIME,na=False)
    url_h=d['url'].str.contains(HORMUZ,na=False)
    contextual=text.str.contains(CONTEXT,na=False)
    d['topic_evidence']=np.select([phrase,h&sea,h,url_h,contextual],
        ['explicit_strait_phrase_metadata','hormuz_and_maritime_terms_metadata','hormuz_ambiguous_context','hormuz_in_URL_only','regional_shipping_context_only'],default='no_direct_topic_signal')
    d['topic_evidence']=pd.Series(d['topic_evidence'],index=d.index)
    d['type_hint']='no_nonprose_path_hint'
    path=d['url'].str.replace(r'^https?://[^/]+','',regex=True).str.split('?',n=1).str[0].str.split('#',n=1).str[0]
    for label,pattern in TYPE_PATTERNS.items():
        mask=d['type_hint'].eq('no_nonprose_path_hint') & path.str.contains(pattern,na=False)
        d.loc[mask,'type_hint']=label
    d.loc[d['type_hint'].eq('no_nonprose_path_hint') & d['title'].str.match(r'^\s*flash\s*:',case=False),'type_hint']='flash_title_hint'
    pay=d['nc_paid_content'].str.strip().str.lower();lang=d['nc_language'].str.strip().str.lower()
    dt=pd.to_datetime(d['published_date'],format='mixed',errors='coerce',utc=True)
    d['month']=dt.dt.strftime('%Y-%m').fillna('unknown');d['UTC_day']=dt.dt.strftime('%Y-%m-%d').fillna('unknown')
    valid_id=d['article_id'].str.fullmatch(r'[a-f0-9]{16}')
    valid_url=d['url'].str.match(r'^https?://[^/\s?#]+(?:[/?#]|$)')
    host=d['url'].str.extract(r'^https?://([^/:?#]+)',expand=False).fillna('').str.lower()
    out=dt.notna() & ~dt.between(pd.Timestamp('2026-01-01',tz='UTC'),pd.Timestamp('2026-09-01',tz='UTC'),inclusive='left')
    exclusion=[(pay.isin(['true','1','yes']),'paid_metadata_explicit'),(out,'outside_research_period'),(~valid_url,'invalid_URL')]
    reviews=[(~valid_id,'invalid_existing_article_id'),(dt.isna(),'date_unknown'),(host.ne(d['domain']),'URL_domain_mismatch'),
             (~pay.isin(['false','0','no','true','1','yes']),'payment_metadata_unknown'),
             (~lang.eq('en'),'language_metadata_unconfirmed'),
             (d['type_hint'].ne('no_nonprose_path_hint'),'nonprose_or_special_type_hint_needs_review'),
             (d['technical_risk'].ne(''),'exact_historical_access_or_AI_quality_review'),
             (d['source_review_hint'].eq('aggregator_or_republication_flag'),'source_republication_review'),
             (d['title'].str.strip().eq(''),'missing_title'),
             (~(phrase | (h&sea)),'topic_context_review')]
    d['selection_status']='ready';d['reason']='metadata_eligible_body_unverified'
    # First reason is exclusive, additional dimensions remain separate.
    for mask,reason in reviews:
        m=mask & d['selection_status'].eq('ready');d.loc[m,['selection_status','reason']]=['review',reason]
    for mask,reason in reversed(exclusion):d.loc[mask,['selection_status','reason']]=['exclude',reason]
    d['priority']=np.where(phrase,1,np.where(h&sea,2,3))
    return d

def schedule(d,seed=20261107,lookahead=64):
    """Order ALL ready records; tiny month warm-up, then proportional virtual time.

    Priority applies within each month, not as a corpus-size cap. A bounded buffer
    avoids adjacent same-domain tasks when an alternative is available.
    """
    if len(d)==0:return np.array([],dtype=np.int64),dict(adjacent_same_domain=0,warmup_months=0)
    a=d[['article_id','month','domain','priority']].sort_values('article_id',kind='stable').copy()
    rng=np.random.Generator(np.random.PCG64(seed));a['_random']=rng.random(len(a))
    a=a.sort_values(['month','priority','_random','article_id'],kind='stable')
    a['_k']=a.groupby('month',sort=False).cumcount()
    a['_virtual']=(a['_k']+.5)/a.groupby('month',sort=False)['article_id'].transform('size')
    warm=a[a['_k'].eq(0)].sort_values('_random',kind='stable')
    rest=a[a['_k'].ne(0)].sort_values(['_virtual','_random','article_id'],kind='stable')
    order=np.concatenate([warm.index.to_numpy(),rest.index.to_numpy()])
    domain=d['domain'].to_dict();pending=deque();nextpos=0;prev=None;result=[];same=0
    for _ in range(len(order)):
        while len(pending)<lookahead and nextpos<len(order):pending.append(int(order[nextpos]));nextpos+=1
        chosen=0
        if domain[pending[0]]==prev:
            for j,x in enumerate(pending):
                if domain[x]!=prev:chosen=j;break
        idx=pending[chosen];del pending[chosen]
        if domain[idx]==prev:same+=1
        prev=domain[idx];result.append(idx)
    return np.array(result,dtype=np.int64),dict(adjacent_same_domain=same,warmup_months=len(warm))
