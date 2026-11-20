"""Compile only declared and supported ontology/shape structures.

Unparsed text rules are reported as explanatory/runtime rules.  A comment is
never emitted as an executable OWL axiom.  Outputs go to a new directory;
source JSON, manually maintained Shapes, and existing releases stay intact.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from rdflib import Graph, BNode, URIRef, Literal
from rdflib.collection import Collection
from rdflib.namespace import RDF, RDFS, OWL, XSD, SH

ROOT=Path(__file__).resolve().parent
B='https://local.ontology/coast-raid-k#'
A='https://local.ontology/coast-raid-k/agent/1.0.0#'

def compile_package(source=ROOT):
    base=json.loads((source/'tbox.json').read_text(encoding='utf-8'))
    ext=json.loads((source/'agent.extension.json').read_text(encoding='utf-8'))
    graph=Graph();shapes=Graph()
    for g in [graph,shapes]:
        for p,ns in [('crk',B),('ag',A),('owl',OWL),('rdf',RDF),('rdfs',RDFS),('xsd',XSD),('sh',SH)]: g.bind(p,ns)
    classes={c['id']:c.get('iri',B+c['id']) for c in base['classes']+ext['classes']}
    report={'compilerVersion':'1.0.0','sourceHashes':{},'rules':[],
            'limits':'Explicit structures only; no free-form Manchester parser. OWL validation remains a separate scoped run.'}
    for name in ['tbox.json','agent.extension.json','agent.shapes.ttl']:
        report['sourceHashes'][name]=hashlib.sha256((source/name).read_bytes()).hexdigest()
    def iri(value,ns):
        if value.startswith(('http://','https://','urn:')): return URIRef(value)
        for prefix,namespace in [('xsd:',XSD),('owl:',OWL),('rdf:',RDF),('rdfs:',RDFS)]:
            if value.startswith(prefix): return URIRef(str(namespace)+value[len(prefix):])
        return URIRef(ns+value)
    def cls(value): return URIRef(classes[value])
    def restriction(prop,predicate,value):
        node=BNode();graph.add((node,RDF.type,OWL.Restriction));graph.add((node,OWL.onProperty,prop));graph.add((node,predicate,value));return node
    def minshape(subject,props,minimum=1):
        node=BNode();shapes.add((node,RDF.type,SH.NodeShape));shapes.add((node,SH.targetClass,subject))
        for prop in props:
            p=BNode();shapes.add((node,SH.property,p));shapes.add((p,SH.path,prop));shapes.add((p,SH.minCount,Literal(minimum,datatype=XSD.integer)))
    for doc,ns in [(base,B),(ext,A)]:
        ont=URIRef(ns);graph.add((ont,RDF.type,OWL.Ontology));graph.add((ont,OWL.versionInfo,Literal(str(doc.get('version','1.0.0')))))
        for c in doc['classes']:
            subject=iri(c.get('iri',ns+c['id']),ns);graph.add((subject,RDF.type,OWL.Class))
            if c.get('label'):graph.add((subject,RDFS.label,Literal(c['label'],lang='zh')))
            if c.get('comment'):graph.add((subject,RDFS.comment,Literal(c['comment'],lang='zh')))
            for parent in c.get('subClassOf',[]): graph.add((subject,RDFS.subClassOf,iri(parent,ns)))
            for k,p in [('equivalentTo',OWL.equivalentClass),('seeAlso',RDFS.seeAlso)]:
                for target in c.get(k,[]): graph.add((subject,p,iri(target,ns)))
        for key,kind in [('objectProperties',OWL.ObjectProperty),('dataProperties',OWL.DatatypeProperty)]:
            for p in doc[key]:
                subject=iri(p.get('iri',ns+p['id']),ns);graph.add((subject,RDF.type,kind))
                for k,pred in [('domain',RDFS.domain),('range',RDFS.range),('inverseOf',OWL.inverseOf)]:
                    if p.get(k):graph.add((subject,pred,iri(p[k],ns)))
                if p.get('comment'):graph.add((subject,RDFS.comment,Literal(p['comment'],lang='zh')))
                if p.get('deprecated'):graph.add((subject,OWL.deprecated,Literal(True)))
        for left,right in doc.get('disjoint',[]):graph.add((iri(left,ns),OWL.disjointWith,iri(right,ns)))
    for ax in base.get('axioms',[]):
        aid=ax['id'];carrier='OWL';status='emitted';reason='supported reviewed structure'
        expected={
            'entity-has-quality':'Entity SubClassOf hasState some Quality',
            'algorithm-no-claim':'Algorithm SubClassOf claims value false',
            'monitor-writes-permit':'Monitor SubClassOf writes some Permit',
            'shell-no-permit':'ShellTemplate SubClassOf not (writes some Permit)',
            'unk-not-listable':'Identity and (iden value 不明) SubClassOf listable value false',
        }
        if aid in expected and ax.get('manchester')!=expected[aid]:
            report['rules'].append({'id':aid,'source':'tbox.json','carrier':'explanatory','status':'not-compiled','reason':'supported rule id has unreviewed changed text','original':ax})
            continue
        if aid=='entity-has-quality': graph.add((cls('Entity'),RDFS.subClassOf,restriction(URIRef(B+'hasState'),OWL.someValuesFrom,cls('Quality'))))
        elif aid=='algorithm-no-claim': graph.add((cls('Algorithm'),RDFS.subClassOf,restriction(URIRef(B+'claims'),OWL.hasValue,Literal(False))))
        elif aid=='monitor-writes-permit': graph.add((cls('Monitor'),RDFS.subClassOf,restriction(URIRef(B+'writes'),OWL.someValuesFrom,cls('Permit'))))
        elif aid=='shell-no-permit':
            r=restriction(URIRef(B+'writes'),OWL.someValuesFrom,cls('Permit'));n=BNode();graph.add((n,OWL.complementOf,r));graph.add((cls('ShellTemplate'),RDFS.subClassOf,n))
        elif aid=='unk-not-listable':
            n=BNode();head=BNode();Collection(graph,head,[cls('Identity'),restriction(URIRef(B+'iden'),OWL.hasValue,Literal('不明'))]);graph.add((n,OWL.intersectionOf,head));graph.add((n,RDFS.subClassOf,restriction(URIRef(B+'listable'),OWL.hasValue,Literal(False))))
        else: carrier='runtime/explanatory';status='not-compiled';reason='free text needs an explicitly reviewed executable carrier; original text retained'
        report['rules'].append({'id':aid,'source':'tbox.json','carrier':carrier,'status':status,'reason':reason,'original':ax})
    for ax in ext.get('axioms',[]):
        kind=ax['kind'];carrier='SHACL';status='emitted';reason='explicit structured rule'
        if kind=='disjoint': graph.add((cls(ax['left']),OWL.disjointWith,cls(ax['right'])));carrier='OWL'
        elif kind=='minCardinality':
            graph.add((cls(ax['subject']),RDFS.subClassOf,restriction(URIRef(A+ax['property']),OWL.minCardinality,Literal(ax['minCount'],datatype=XSD.nonNegativeInteger))))
            minshape(cls(ax['subject']),[URIRef(A+ax['property'])],ax['minCount']);carrier='OWL + SHACL (independent semantics)'
        elif kind in ['requiredDataProperty','requiredProperties','requiredDataProperties']:
            props=ax.get('properties',[ax.get('property')]);minshape(cls(ax['subject']),[URIRef(A+p) for p in props if p])
        elif kind=='requiredFunctionDomain':carrier='JSON Schema + manual SHACL';status='referenced';reason='schema const and Gate SPARQL constraint in agent.shapes.ttl; not a guessed OWL axiom'
        elif kind=='requiredReference':carrier='JSON Schema conditional';status='referenced';reason='terminal ApprovalRequest status conditional requires decisionIri; not unconditional minCount'
        elif kind=='mapping':carrier='mapping/explanatory';status='not-compiled';reason='mapping does not entail owl:equivalentClass'
        else:carrier='explanatory';status='not-compiled';reason='unsupported rule kind'
        report['rules'].append({'id':ax['id'],'source':'agent.extension.json','carrier':carrier,'status':status,'reason':reason,'original':ax})
    report['counts']={'ontologyTriples':len(graph),'generatedShapeTriples':len(shapes),'rules':len(report['rules'])}
    report['status']='compiled-supported-subset'
    return graph,shapes,report

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source-dir',type=Path,default=ROOT);parser.add_argument('--out-dir',type=Path,required=True)
    args=parser.parse_args(argv)
    if args.out_dir.exists():parser.error('--out-dir must not exist')
    graph,shapes,report=compile_package(args.source_dir.resolve())
    args.out_dir.mkdir(parents=True)
    graph.serialize(args.out_dir/'ontology.ttl',format='turtle');shapes.serialize(args.out_dir/'generated.shapes.ttl',format='turtle')
    (args.out_dir/'semantic-export-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report['counts']));return 0

if __name__=='__main__':raise SystemExit(main())
