"""Explicit JSON-to-RDF conversion with loss and reference reporting.

This converter never changes the source package, asserts missing reference
types, or turns a numeric simulation clock into a wall-clock dateTime.
Outputs must go to a fresh directory.  JSON validation and graph validation
are separate operations performed by the validation harness.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS, XSD
from identity import build_index, canonical_iri, is_absolute_iri, resolve
from convert_sources import records_from_package

ROOT=Path(__file__).resolve().parent
A='https://local.ontology/coast-raid-k/agent/1.0.0#'
B='https://local.ontology/coast-raid-k#'
DCT='http://purl.org/dc/terms/'

def json_hash(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()

def unpack_documents(value):
    if isinstance(value,list):
        return [r for x in value for r in unpack_documents(x)]
    if not isinstance(value,dict): raise ValueError('input must be a JSON object or list')
    if 'instances' in value and isinstance(value['instances'],list): return unpack_documents(value['instances'])
    if value.get('type') and value.get('id'): return [value]
    rows=[]
    for key in ['run','agents','observations','beliefs','decisions','actionRequests','authorizations','executions','auditEvents','decisionDrafts','approvalRequests','humanDecisions','triggers']:
        if key in value: rows.extend(unpack_documents(value[key]))
    return rows

def pointer_get(obj,path):
    current=obj
    for part in path.strip('/').split('/'):
        token=part.replace('~1','/').replace('~0','~')
        if not isinstance(current,dict) or token not in current: return False,None
        current=current[token]
    return True,current

def convert_documents(documents, mapping, identity_registry):
    """Return ``(Graph, report)`` from explicit source documents.

    Documents may optionally carry ``_identity`` metadata supplied by the
    package source reader.  That metadata is excluded from source hashing and
    field coverage.  Missing reference records remain unresolved in reports.
    """
    graph=Graph();graph.bind('ag',A);graph.bind('crk',B);graph.bind('rdf',RDF);graph.bind('xsd',XSD)
    rows=[]; input_index=[]
    classes=mapping['classIris']
    for raw in documents:
        raw=dict(raw);meta=raw.pop('_identity',{})
        typ=meta.get('type') or raw.get('type');ident=meta.get('id') or raw.get('id')
        if typ=='Class': continue
        if typ not in classes: raise ValueError(f'unknown document class {typ!r}')
        identity={**meta,'type':typ,'id':ident}
        if raw.get('iri'): identity['iri']=raw['iri']
        if raw.get('runId'): identity['runId']=raw['runId']
        iri=canonical_iri(identity,identity_registry)
        rows.append((raw,identity,iri,classes[typ]));input_index.append({**identity,'iri':iri})
    # Include registered source identities only for alias resolution.  They
    # do not add RDF types or pretend that referenced data was provided.
    registry_entries=[dict(e) for e in identity_registry.get('entries',[])]
    current_iris={x['iri'] for x in input_index}
    index=build_index([e for e in registry_entries if e['iri'] not in current_iris]+input_index,identity_registry)
    provided={iri for _,_,iri,_ in rows}
    by_type_id={}
    for _,meta,iri,_ in rows: by_type_id.setdefault((meta['type'],meta['id']),[]).append(iri)
    report={'mappingVersion':mapping['mappingVersion'],'identityVersion':identity_registry['registryVersion'],
            'inputHash':json_hash([r for r,_,_,_ in rows]),'documents':len(rows),'subjects':[],
            'mapped':[],'unmapped':[],'omitted':[],'referenceWarnings':[],'losses':[],
            'validationStatus':'not-executed','policy':'No missing target types or temporal anchors are manufactured.'}

    def reference(value,m,subject,path):
        if not isinstance(value,str) or not value:
            raise ValueError(f'{subject} {path}: IRI reference must be a nonempty string')
        iri=value
        if m.get('referencePolicy')=='resolve-Run-in-input-or-registry' and not is_absolute_iri(value):
            candidates=by_type_id.get(('Run',value),[])
            if len(candidates)>1: raise ValueError(f'ambiguous Run short ID {value}')
            if candidates: iri=candidates[0]
            else:
                registered=[e['iri'] for e in registry_entries if e['type']=='Run' and e['id']==value]
                if len(registered)>1: raise ValueError(f'ambiguous Run registration {value}')
                iri=registered[0] if registered else canonical_iri({'type':'Run','id':value},{**identity_registry,'entries':[]})
        elif not is_absolute_iri(value):
            resolution=resolve(B+value,index)
            if resolution['status']!='resolved': raise ValueError(f'{subject} {path}: unresolved or ambiguous short reference {value!r}')
            iri=resolution['record'].iri
        else:
            resolution=resolve(value,index)
            if resolution['status']=='ambiguous': raise ValueError(f'{subject} {path}: ambiguous legacy IRI {value}')
            if resolution['status']=='resolved': iri=resolution['record'].iri
        if iri not in provided:
            report['referenceWarnings'].append({'subject':subject,'jsonPointer':path,'iri':iri,'status':'target-record-not-supplied'})
        return URIRef(iri)

    def scalar(value,datatype,subject,path):
        if isinstance(value,(dict,list)): raise ValueError(f'{subject} {path}: structured value requires an explicit structured mapping')
        dt=URIRef(datatype) if datatype else None
        if dt==XSD.decimal:
            if isinstance(value,bool) or not isinstance(value,(int,float,Decimal)): raise ValueError(f'{subject} {path}: expected numeric decimal')
            return Literal(Decimal(str(value)),datatype=XSD.decimal)
        if dt==XSD.boolean and not isinstance(value,bool): raise ValueError(f'{subject} {path}: expected boolean')
        return Literal(value,datatype=dt)

    for raw,meta,iri,class_iri in rows:
        subject=URIRef(iri); graph.add((subject,RDF.type,URIRef(class_iri)))
        report['subjects'].append({'iri':iri,'classIri':class_iri,'sourceHash':json_hash(raw),'file':meta.get('file')})
        relevant=[m for m in mapping['mappings'] if m['classIri']==class_iri]
        handled=set()
        for m in relevant:
            path=m['jsonPointer'];exists,value=pointer_get(raw,path)
            if not exists: continue
            handled.add(path.split('/')[1].replace('~1','/').replace('~0','~'))
            record={'subject':iri,'classIri':class_iri,'jsonPointer':path,'value':value}
            if m['status']=='identity':
                report['mapped'].append({**record,'predicateIri':None,'role':'subject-identity'});continue
            if m['status']!='mapped' or not m.get('predicateIri'):
                report['unmapped'].append(record);continue
            if value is None:
                report['omitted'].append({**record,'reason':'explicit-null'});continue
            predicate=URIRef(m['predicateIri']);kind=m['nodeKind']
            if kind=='ClassReference':
                if value != meta['type']: raise ValueError(f'{iri}: source type conflicts with registry class')
                graph.add((subject,RDF.type,URIRef(class_iri)))
            elif kind in ['TimeInterval','ProvenanceRecord','EffectRecord']:
                if not isinstance(value,dict): raise ValueError(f'{iri} {path}: expected structured object')
                node=BNode('n'+json_hash([iri,path])[:30]);graph.add((subject,predicate,node))
                graph.add((node,RDF.type,URIRef(A+'TimeInterval' if kind=='TimeInterval' else A+'Effect' if kind=='EffectRecord' else B+'InformationEntity')))
                # rdf:JSON is an explicit archival representation of the
                # structure, never an inferred datatype for arbitrary fields.
                graph.add((node,RDF.value,Literal(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')),datatype=RDF.JSON)))
                if kind=='TimeInterval':
                    for k,pred,dt in [('from','intervalFrom',XSD.decimal),('to','intervalTo',XSD.decimal),('clock','timeClock',XSD.string),('unit','timeUnit',XSD.string)]:
                        if k in value and value[k] is not None: graph.add((node,URIRef(A+pred),scalar(value[k],str(dt),iri,path+'/'+k)))
                    if not value.get('clock'): report['losses'].append({**record,'reason':'clock-not-registered; numeric time retained without dateTime conversion'})
                    if not value.get('unit'): report['losses'].append({**record,'reason':'time-unit-not-registered; no unit invented'})
                elif kind=='ProvenanceRecord':
                    for k,pred in [('source',DCT+'source'),('sha256',A+'sourceSha256'),('hashStatus',A+'hashStatus')]:
                        if k in value: graph.add((node,URIRef(pred),Literal(value[k],datatype=XSD.string)))
            else:
                values=value if isinstance(value,list) else [value]
                if not values: report['omitted'].append({**record,'reason':'explicit-empty-array; zero-rdf-values'})
                seen_values=set()
                for v in values:
                    term=reference(v,m,iri,path) if kind=='IRI' else scalar(v,m.get('datatype'),iri,path)
                    if term in seen_values: report['losses'].append({**record,'reason':'duplicate array value collapsed by explicit unordered-set mapping'})
                    seen_values.add(term);graph.add((subject,predicate,term))
            report['mapped'].append({**record,'predicateIri':str(predicate),'nodeKind':kind})
        for key,value in raw.items():
            if key not in handled:
                report['unmapped'].append({'subject':iri,'classIri':class_iri,'jsonPointer':'/'+key,'value':value,'reason':'field-not-registered'})
    report['triples']=len(graph)
    report['counts']={k:len(report[k]) for k in ['mapped','unmapped','omitted','referenceWarnings','losses']}
    report['status']='converted-with-unmapped-or-unresolved-values' if report['unmapped'] or report['referenceWarnings'] or report['losses'] else 'converted'
    return graph,report

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-dir',type=Path,default=ROOT);p.add_argument('--out-dir',type=Path,required=True);p.add_argument('--input',type=Path)
    args=p.parse_args(argv)
    source=args.source_dir.resolve();out=args.out_dir.resolve()
    if out.exists(): p.error('--out-dir must not exist; source and prior outputs are preserved')
    mapping=json.loads((source/'semantic-mapping.json').read_text(encoding='utf-8'))
    registry=json.loads((source/'identity-registry.json').read_text(encoding='utf-8'))
    docs=unpack_documents(json.loads(args.input.read_text(encoding='utf-8'))) if args.input else [{**r,'_identity':m} for r,m in records_from_package(source)]
    graph,report=convert_documents(docs,mapping,registry)
    out.mkdir(parents=True);graph.serialize(out/'abox.ttl',format='turtle')
    (out/'conversion-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'documents':report['documents'],'triples':report['triples'],**report['counts']}))
    return 0

if __name__=='__main__': raise SystemExit(main())
