"""Refresh reviewed copy without fetching or posting; update public docs."""
from pathlib import Path
import json
import fastnews247_mvp as n
root=n.ROOT
p=root/'outputs/fastnews247/last_run.json'
d=n.load_json(p,{})
for item in d.get('selected',[]):
    item['draft'], issues=n.draft_post(item,item['score'],item['tags'])
    if issues: raise RuntimeError(str(issues))
    print(item['draft'])
n.save_json(root/'outputs/fastnews247/reviewed_draft.json',d)
for folder in ('docs','prompts'):
    for path in (root/folder).glob('*'):
        if path.is_file() and 'fast' in path.name.lower():
            text=path.read_text(encoding='utf-8')
            text=text.replace('FASTNEWS 247','Tin nhanh 247').replace('Fast News 247','Tin nhanh 247').replace('#Fastnews','#Tinnhanh247')
            note='\n\n> Production authority: see docs/TIN_NHANH_247_REPAIR_REPORT.md. Historical setup instructions below may be superseded; paths, task ID and @fastnews247vn remain unchanged.\n'
            if 'Production authority:' not in text: text+=note
            path.write_text(text,encoding='utf-8')
