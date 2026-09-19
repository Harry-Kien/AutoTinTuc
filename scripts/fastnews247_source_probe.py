"""Read-only source evidence; never sends or mutates production state."""
import json
import concurrent.futures
from pathlib import Path
import fastnews247_mvp as n
c=n.load_json(n.CONFIG_PATH,{})
rows=[]
def probe(feed):
    items=n.parse_feed(feed)
    return [dict(i,score=n.score_item(i,c)[0],freshness=n.freshness_issue(i,c)) for i in items if not n.freshness_issue(i,c)]
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    for result in pool.map(probe,c['feeds']): rows.extend(result)
for i in rows:
    print(i['score'],i['source'],i['published'],i['title'])
n.save_json(n.ROOT/'outputs/fastnews247/fresh_source_probe.json',rows)
