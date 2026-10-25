#!/usr/bin/env python3
"""Fail closed before publication; keep ingestion dates separate from event dates."""
import json
import re
from datetime import date,timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parent

def validate():
    text=(PROJECT/'Nigeria_Events_Map.html').read_text()
    assert len(text)>500000 and '</html>' in text.lower(), 'Incomplete map HTML'
    assert 'L.map(' in text, 'Map application missing'
    arrays={}
    for name in ['EVENTS','GDELT_EVENTS']:
        m=re.search(r'const '+name+r'\s*=\s*(\[.*?\]);',text,re.S)
        assert m, 'Missing '+name
        arrays[name]=json.loads(m[1])
        assert len(arrays[name])>100, 'Unexpectedly empty '+name
        for event in arrays[name]:
            date.fromisoformat(event['d'])
            assert -90<=event['la']<=90 and -180<=event['lo']<=180
    state=json.loads((ROOT/'.gdelt_state.json').read_text())
    assert not state.get('missing'), 'GDELT has missing daily files'
    through=date.today()-timedelta(days=1)
    first=date.fromisoformat(min(x['d'] for x in arrays['GDELT_EVENTS']))
    required={(first+timedelta(days=i)).strftime('%Y%m%d') for i in range((through-first).days+1)}
    missing=required-set(state['ingested'])
    assert not missing, 'Daily files not ingested: '+', '.join(sorted(missing))
    png=PROJECT/'Nigeria_Events_Map_LastWeek_latest.png'
    assert png.read_bytes().startswith(b'\x89PNG\r\n\x1a\n') and png.stat().st_size>100000
    assert png.stat().st_mtime >= (PROJECT/'Nigeria_Events_Map.html').stat().st_mtime, 'PNG not freshly rendered'
    print('Validated:',len(arrays['EVENTS']),'UCDP;',len(arrays['GDELT_EVENTS']),'GDELT; all daily files through',through)
    return arrays

if __name__=='__main__':
    validate()
