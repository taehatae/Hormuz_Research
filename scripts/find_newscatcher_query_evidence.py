"""Inspect local notebook source and local Git history; print only safe search settings."""
from pathlib import Path
import ast,json,re,subprocess,os
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'hormuz_2026_data/runs/body_pilot_200_20261107_01'
SAFE={'q','query','queries','search_queries','newscatcher_queries','query_list','nc_queries','keywords','search_in','lang','language','from','to','from_','to_','from_date','to_date','start_date','end_date','start','end','sort_by','page_size','theme'}
def git(*args):
    r=subprocess.run(['git','-c','core.quotepath=false',*args],cwd=ROOT,env=dict(os.environ,GIT_OPTIONAL_LOCKS='0'),capture_output=True)
    return r.stdout.decode('utf-8','replace') if r.returncode==0 else ''
def scalar(n):
    try:v=ast.literal_eval(n)
    except Exception:return None
    return v if isinstance(v,(str,int,float,bool)) or (isinstance(v,(list,tuple)) and all(isinstance(a,str) for a in v)) else None
def inspect(content,source,revision):
    try:nb=json.loads(content)
    except Exception:return []
    evidence=[]
    for cell,c in enumerate(nb.get('cells',[]),1):
        if c.get('cell_type')!='code':continue
        text=''.join(c.get('source',[]))
        clean='\n'.join('' if s.lstrip().startswith(('%','!')) else s for s in text.splitlines())
        try:tree=ast.parse(clean)
        except SyntaxError:continue
        api_context=bool(re.search(r'newscatcher|api\.newscatcher|newscatcherapi',text,re.I))
        values=[]
        for n in ast.walk(tree):
            if isinstance(n,ast.Assign):
                for target in n.targets:
                    if isinstance(target,ast.Name) and target.id.lower() in SAFE:
                        v=scalar(n.value)
                        if v is not None:values.append(dict(line=n.lineno,setting=target.id,value=v,syntax='assignment'))
            elif isinstance(n,ast.Dict):
                for k,v in zip(n.keys,n.values):
                    key=scalar(k) if k else None;val=scalar(v)
                    if isinstance(key,str) and key.lower() in SAFE:
                        values.append(dict(line=n.lineno,setting=key,value=val if val is not None else ast.unparse(v)[:150],syntax='dictionary search parameter'))
            elif isinstance(n,ast.Call):
                for k in n.keywords:
                    if k.arg and k.arg.lower() in SAFE:
                        val=scalar(k.value)
                        values.append(dict(line=n.lineno,setting=k.arg,value=val if val is not None else ast.unparse(k.value)[:150],syntax='call keyword'))
        if values:evidence.append(dict(file=source,git_revision=revision,cell_1based=cell,API_context_in_cell=api_context,settings=values))
    return evidence
def main():
    evidence=[];inspected=[];seen=set()
    for p in ROOT.glob('*.ipynb'):
        content=p.read_text(encoding='utf-8');evidence+=inspect(content,p.name,'working_tree');inspected.append(dict(file=p.name,revision='working_tree'))
    commits=git('log','--all','--format=%H','--','*.ipynb').splitlines()
    for rev in commits:
        for name in git('ls-tree','-r','--name-only',rev).splitlines():
            if not name.endswith('.ipynb'):continue
            blob=git('rev-parse',rev+':'+name).strip()
            if blob in seen:continue
            seen.add(blob);content=git('show',rev+':'+name)
            evidence+=inspect(content,name,rev);inspected.append(dict(file=name,revision=rev,blob=blob))
    result=dict(scope='working notebooks and local Git notebook source history; no API/network; no environment or credential values copied',inspected=inspected,evidence=evidence,
                limitation='Historical code settings are evidence of code configuration, not proof a specific raw CSV was collected using that exact revision. Notebook output cells not executed or copied.')
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'newscatcher_query_evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps([dict(file=e['file'],revision=e['git_revision'][:10],cell=e['cell_1based'],settings=[s for s in e['settings'] if s['setting'].lower() in {'q','query','queries','search_queries','nc_queries','search_in','lang'}]) for e in evidence if any(s['setting'].lower() in {'q','query','queries','search_queries','nc_queries','search_in','lang'} for s in e['settings'])],ensure_ascii=False,indent=2))
if __name__=='__main__':main()
