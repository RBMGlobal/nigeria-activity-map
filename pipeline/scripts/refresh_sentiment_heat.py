#!/usr/bin/env python3
"""Rebuild news-reporting heat only; do not invent polling or X observations."""
from collections import defaultdict
from datetime import datetime,timezone
import json
import sys
from pathlib import Path
from validate_map_release import validate

def main():
    events=validate()['GDELT_EVENTS']
    selected=[e for e in events if int(e['r']) in (18,19,20)]
    grid=defaultdict(int)
    for e in selected:
        grid[(round(e['la']*4)/4,round(e['lo']*4)/4)]+=max(1,int(e.get('n',1)))
    result={'from':min(e['d'] for e in selected),'to':max(e['d'] for e in selected),
        'count':len(grid),'raw':len(selected),
        'points':[[la,lo,n] for (la,lo),n in sorted(grid.items())],
        'method':'GDELT CAMEO roots 18-20, grouped on a 0.25-degree grid; weight is summed article count. Machine-coded reporting, not verified incidents or opinion polling.',
        'source':'https://rbmglobal.co.uk/nigeria-activity-map/'}
    out=Path(__file__).resolve().parent.parent/'Published'/'sentiment-gdelt.json'
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print('Prepared local heat file:',out)
    print('Heat:',len(selected),'records;',len(grid),'grid cells; through',result['to'])

if __name__=='__main__':
    main()
