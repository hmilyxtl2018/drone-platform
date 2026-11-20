"""Read explicit source records for the identity registry; no writes."""
from __future__ import annotations
import json
from pathlib import Path

def records_from_package(base: Path, *, include_classes=False):
    reg=json.loads((base/'identity-registry.json').read_text(encoding='utf-8'))
    cache={}
    for entry in reg['entries']:
        if entry['kind']=='Class' and not include_classes: continue
        name=entry['file']
        if name not in cache: cache[name]=json.loads((base/name).read_text(encoding='utf-8'))
        doc=cache[name]; typ=entry['type']; ident=entry['id']; candidates=[]
        if typ=='Class': candidates=doc.get('classes',[])
        elif name=='catalog.json':
            candidates=[doc] if typ=='Package' else [{'id':x,'type':'Exclude'} for x in doc.get('excludes',[])]
        elif name=='theater.json': candidates=doc.get('sites',[])+doc.get('bodies',[])+doc.get('platformTypes',[])
        elif name=='gis.json':
            if typ=='Shoreline': candidates=[doc['shoreline']]
            elif typ=='Region': candidates=doc.get('regions',[])
            elif typ=='Membership': candidates=[{'id':str(x['of'])+'∈'+str(x['region']),**x} for x in doc.get('membership',[])]
        elif name=='graph.json': candidates=[doc.get('shoreTdl',{})] if typ=='Needline' else doc.get('otherLinks',[])
        elif name=='rules.json': candidates=doc.get('rules',[])
        elif name=='algorithms.json': candidates=doc.get('tools',[]) if typ=='Algorithm' else doc.get('functions',[])
        elif name=='constraints.json': candidates=[{'id':k,**v} for k,v in doc.get('clouds',{}).items()] if typ=='CloudDef' else doc.get('invariants',[])
        elif name=='shells.json': candidates=doc.get('templates',[]) if typ=='ShellTemplate' else [{'id':doc.get('permitWriter'),'permitWriter':doc.get('permitWriter')}]
        elif name=='agent.instances.json': candidates=doc.get('instances',[])
        else:
            candidates=doc.get('instances',[]) if isinstance(doc,dict) else []
        found=[r for r in candidates if r.get('id')==ident]
        if len(found)!=1: raise ValueError(f'expected one source record: {name} {typ}/{ident}; found {len(found)}')
        record=found[0]
        if typ=='Membership':
            # The display ID is synthesized by inventory from (of, region).
            # It belongs to identity metadata, not the original closed JSON
            # record, so do not feed it back into JSON Schema validation.
            record={k:v for k,v in record.items() if k!='id'}
        yield record,entry
