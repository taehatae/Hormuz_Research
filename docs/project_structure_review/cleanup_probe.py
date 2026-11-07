"""Child process: bounded imports only, all writes/network/subprocesses denied."""
import sys,os,json,importlib,traceback,csv
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
VENDOR=ROOT/'hormuz_2026_data/body_collection_pilot_20261102_01/vendor'
sys.dont_write_bytecode=True
denied=[]
with (Path(__file__).resolve().parent/'deletion_candidates.csv').open(encoding='utf-8-sig',newline='') as f:
    candidate_paths={str((ROOT/r['current_relative_path']).resolve()).casefold() for r in csv.DictReader(f) if '/vendor/' in r['current_relative_path']}
candidate_reads=set()
def audit(event,args):
    if event in {'socket.connect','socket.bind','socket.getaddrinfo','subprocess.Popen','os.system','os.remove','os.rename','os.mkdir','os.rmdir'}:
        denied.append(event);raise PermissionError('Cleanup probe denies '+event)
    if event=='open':
        mode=args[1];flags=args[2]
        if (isinstance(mode,str) and any(c in mode for c in 'wax+')) or (isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):
            denied.append('write');raise PermissionError('Cleanup probe denies file writes')
        if isinstance(args[0],str) and str(Path(args[0]).absolute()).casefold() in candidate_paths:candidate_reads.add(args[0])
sys.addaudithook(audit)
sys.path.insert(0,str(VENDOR))
names=['requests','bs4','trafilatura','newsplease','langdetect','boto3','botocore','twisted','twisted.internet.defer','zope.interface','hyperlink','incremental','queuelib','certifi','constantly','hjson','ago']
rows=[]
for name in names:
    try:
        obj=importlib.import_module(name)
        checks={}
        if name=='requests':checks['Session_callable']=callable(obj.Session)
        if name=='bs4':checks['BeautifulSoup_callable']=callable(obj.BeautifulSoup)
        if name=='trafilatura':checks['extract_callable']=callable(obj.extract)
        if name=='newsplease':checks['from_html_callable']=callable(obj.NewsPlease.from_html)
        if name=='langdetect':checks['detect_callable']=callable(obj.detect)
        if name=='requests':checks['prepare_request_no_network']=obj.Request('GET','https://example.invalid/').prepare().method=='GET'
        if name=='bs4':checks['local_markup_parser']=obj.BeautifulSoup('<p>probe</p>','html.parser').p.get_text()=='probe'
        if name=='twisted.internet.defer':
            d=obj.Deferred();values=[];d.addCallback(values.append);d.callback('probe');checks['deferred_callback']=values==['probe']
        if name=='hjson':checks['parse_local_literal']=obj.loads('{value: 1}')['value']==1
        if name=='hyperlink':checks['local_URL_parser']=obj.URL.from_text('https://example.invalid/a').host=='example.invalid'
        if name=='incremental':checks['version_constructor']=obj.Version('probe',1,2,3).major==1
        if name=='queuelib':
            q=importlib.import_module('queuelib.queue').FifoMemoryQueue();q.push(b'probe');checks['memory_queue']=q.pop()==b'probe'
        rows.append(dict(module=name,ok=all(checks.values()),path=getattr(obj,'__file__',None),checks=checks))
    except Exception as e:rows.append(dict(module=name,ok=False,error=type(e).__name__+': '+str(e)))
loaded=[str(Path(m.__file__).resolve()) for m in list(sys.modules.values()) if getattr(m,'__file__',None) and str(m.__file__).casefold().startswith(str(VENDOR).casefold())]
print(json.dumps(dict(results=rows,denied_actions=denied,all_ok=all(r['ok'] for r in rows),loaded_vendor_files=sorted(set(loaded)),candidate_files_read=sorted(candidate_reads)),ensure_ascii=False))
