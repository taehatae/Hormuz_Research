"""Read-only evidence inspection; excludes environment secrets and third-party code."""
from pathlib import Path
import json,csv,re
ROOT=Path(__file__).resolve().parents[1]
def main():
    print('NOTEBOOKS',[p.name for p in ROOT.glob('*.ipynb')])
    for p in ROOT.glob('*.ipynb'):
        nb=json.loads(p.read_text(encoding='utf-8'))
        for i,c in enumerate(nb['cells'],1):
            text=''.join(c.get('source',[]))
            hits=[]
            for j,line in enumerate(text.splitlines(),1):
                if any(k in line.lower() for k in ['newscatcher','api.newscatcher','search_in','lang','query','start_date','end_date','q =','q=']):
                    if re.search('api.?key|token|secret|password|authorization',line,re.I):line='[sensitive-setting line omitted]'
                    hits.append((j,line[:240]))
            if hits:print(p.name,'cell',i,c['cell_type'],hits[:20])
    d=ROOT/'hormuz_2026_data/runs/candidate_selection_20261107_01'
    for n in ['config.json','validation.json']:
        x=json.loads((d/n).read_text(encoding='utf-8'));print(n,x if n=='config.json' else {k:x[k] for k in ['passed','states','queue_rows','queue_domains','months']})
    for n in ['candidate_queue.csv','candidate_screening.csv']:
        with (d/n).open(encoding='utf-8-sig',newline='') as f:
            r=csv.DictReader(f);print(n,'HEADER',r.fieldnames);print('FIRST',next(r))
    print('README', (ROOT/'README.md').read_text(encoding='utf-8')[:1300])
    print('DESIGN',(d/'SELECTION_DESIGN_REPORT.md').read_text(encoding='utf-8')[:4200])
if __name__=='__main__':main()
