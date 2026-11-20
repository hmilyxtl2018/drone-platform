"""Capability acceptance with disposable data; no live providers or credentials."""
import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .capability_profile import CapabilityProfiles, normalize_measurement, validate_profile, profile_hash, quality_for, REGISTRY
from .capability_workflow import CapabilityWorkflow
from .intelligence_store import IntelligenceStore, IntelligenceConflict, IntelligenceValidationError
from .ontology_agent import OntologyAgent
from .equipment_verification import EquipmentVerificationQueue
from .ontology_workbook_import import import_workbook
from .test_ontology_agent import fixture_workbook
from .intelligence_ontology import attach_intelligence_ontology

BODY='Test UAV Block A with sensor-only configuration: communication latency is 10 ms under laboratory conditions.'


class Search:
    def __init__(self, bodies=None, fail=False):self.bodies=bodies if bodies is not None else [BODY];self.fail=fail
    def search(self,*args,**kwargs):
        if self.fail:raise OSError('test provider unavailable')
        return {'fetchedAt':'2026-10-13T00:00:00Z','items':[{'title':'Test UAV report','sourceUri':f'https://example.test/report-{i}','body':body,'contentHash':'untrusted-provider-hash','sourceVersion':'v1'} for i,body in enumerate(self.bodies)],'errors':[],'budget':{'stopReason':'target-reached'}}


class Claims:
    provider_id='test-claim-provider';model='deepseek-v4-pro'
    def __init__(self,values=None,fail=False):self.values=values or [('10 ms',BODY)];self.fail=fail
    def extract(self,document):
        if self.fail:raise OSError('test model unavailable')
        return [{'attributeKey':'communication-latency','sourceSubject':'Test UAV','subject':'Test UAV','value':value,'rawValue':value,'unit':'ms','conditions':'laboratory conditions','variant':'Block A','configuration':'sensor-only','quote':quote,'lineStart':999,'lineEnd':999,'extractionConfidence':.98,'qualifier':'exact','recommendation':{'analysisType':'source-fact'}} for value,quote in self.values]


class Trend:
    configured=True
    def __init__(self,claims=None):self.claim_provider=claims or Claims()


class CapabilityProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.path=root/'test.sqlite';workbook=root/'test.xlsx';fixture_workbook(workbook);import_workbook(workbook,self.path)
        self.store=IntelligenceStore(self.path);self.agent=OntologyAgent(self.store,trend_provider=Trend())
        self.agent.queue=EquipmentVerificationQueue(self.store,Search())
        self.id=next(e['entityId'] for e in self.agent._entities() if e['preferredName']=='Test UAV')
        self.profiles=CapabilityProfiles(self.agent)
    def tearDown(self):self.temp.cleanup()
    def metric(self,p,key,origin=None):return next(m for c in p['capabilities'] for m in c['metrics'] if m['attributeKey']==key and (not origin or m['origin']==origin))
    def collect_extract(self):
        job=next(j for j in self.agent.queue.plan(self.id) if j['targetType']=='systems')
        self.agent.queue.execute(job['verificationId']);return self.agent.extract_attribute_candidates(self.id,{'scope':'systems'})
    def ready(self):
        self.collect_extract();p=self.profiles.save(self.id)['profile'];m=self.metric(p,'communication-latency','candidate');return p,m
    def accept(self,p,m,action='accept'):
        return self.profiles.review(p['profileId'],{'recordId':m['id'],'action':action,'reviewer':'test reviewer','reason':'Matched literal source and object configuration','verifiedConfidence':'high' if action=='accept' else None})
    def publish(self,p,m):return self.profiles.publish(p['profileId'],{'recordIds':[m['id']],'publishedBy':'test reviewer','reason':'Explicit test human decision'})

    def test_archive_coverage_and_confidence_do_not_prove_truth(self):
        p=self.profiles.read(self.id);q=p['quality']
        self.assertEqual(q['expectedFieldCount'],38);self.assertGreater(q['referencedCount'],0)
        self.assertEqual(q['publicSourceCount'],0);self.assertEqual(q['verifiedCount'],0)
        self.assertIsNone(self.metric(p,'communication-latency')['value']);self.assertEqual(validate_profile(p),[])
        loiter=next(e['entityId'] for e in self.agent._entities() if e['preferredName']=='Test Loiter')
        self.assertIs(self.metric(self.profiles.read(loiter),'swarm-compatibility')['value'],False)

    def test_literal_ranges_bounds_approximate_units_and_zero(self):
        spec=REGISTRY['fields']['endurance']
        self.assertEqual(normalize_measurement('90–120 min',90,'min',spec),(None,'h','range',1.5,2.0))
        self.assertEqual(normalize_measurement('约 25 h',999,'h',spec)[:3],(25.0,'h','approximate'))
        self.assertEqual(normalize_measurement('>= 120 min',1,'min',spec),(2.0,'h','at-least',2.0,None))
        self.assertEqual(normalize_measurement('0 h',0,'h',spec)[0],0)
        self.assertIsNone(normalize_measurement(None,None,None,spec)[0])
        self.assertEqual(normalize_measurement('25 hr',25,'hr',spec)[0],25)
        payload=REGISTRY['fields']['payload-capacity']
        self.assertIsInstance(normalize_measurement('575 lb internal + 500 lb external',575,'lb',payload)[0],str)
        self.assertEqual(normalize_measurement('575 lb internal + 500 lb external',575,'lb',payload)[0],'575 lb internal + 500 lb external')

    def test_immutable_snapshot_idempotency_and_corruption_validation(self):
        first=self.profiles.save(self.id);second=self.profiles.save(self.id)
        self.assertFalse(first['duplicate']);self.assertTrue(second['duplicate'])
        with self.store._connect() as con:
            with self.assertRaises(sqlite3.IntegrityError):con.execute("UPDATE ontology_capability_profile SET profile_json='{}'")
            with self.assertRaises(sqlite3.IntegrityError):con.execute('DELETE FROM ontology_capability_profile')
        p=copy.deepcopy(first['profile']);p['quality']['verifiedCount']=10
        self.assertTrue(validate_profile(p))

    def test_publication_requires_each_fact_review_and_live_snapshot_match(self):
        p,m=self.ready()
        with self.assertRaises(IntelligenceConflict):self.publish(p,m)
        self.accept(p,m);published=self.publish(p,m);self.assertFalse(published['duplicate'])
        self.assertTrue(self.publish(p,m)['duplicate'])
        read=self.profiles.read(self.id);metric=self.metric(read,'communication-latency','candidate')
        self.assertEqual(metric['verificationStatus'],'verified');self.assertTrue(metric['comparability']['eligible'])
        layer=self.store.published_ontology();self.assertEqual(layer['admissionCount'],1)
        self.assertEqual({o['type'] for o in layer['objects']},{'EquipmentModel','CapabilityObservation'})
        detailed=self.store.published_ontology(detailed=True)
        detailed_types={o['type'] for o in detailed['objects']}
        self.assertTrue({'EquipmentModel','CapabilityObservation','CapabilityMetric','EvidenceItem','SourceDocument'} <= detailed_types)
        self.assertTrue({'hasCapabilityObservation','hasMetric','supportedBy','derivedFrom'} <= {r['relation'] for r in detailed['relations']})
        boot=attach_intelligence_ontology({},layer,Path(__file__).resolve().parents[1]/'ontology/coast-raid-k')
        self.assertEqual(boot['intelligenceOntology']['objectCount'],2)
        detailed_boot=attach_intelligence_ontology({},detailed,Path(__file__).resolve().parents[1]/'ontology/coast-raid-k')
        self.assertEqual(detailed_boot['intelligenceOntology']['detailLevel'],'full')
        self.assertGreater(detailed_boot['intelligenceOntology']['relationCount'],0)
        compare=self.profiles.compare([self.id],'communication-latency')
        self.assertEqual(compare['cohorts'][0]['sampleSize'],1)

    def test_profile_graph_keeps_typed_metric_evidence_and_source_chain(self):
        p,m=self.ready()
        nodes={node['id']:node for node in p['graph']['nodes']}
        edges=p['graph']['edges']
        self.assertEqual(nodes[m['id']]['type'],'CapabilityObservation')
        self.assertEqual(nodes[m['id']+':metric']['type'],'CapabilityMetric')
        self.assertTrue(any(node['type']=='EvidenceItem' for node in nodes.values()))
        self.assertTrue(any(node['type']=='SourceDocument' for node in nodes.values()))
        self.assertTrue(any(edge['from']==m['id'] and edge['to']==m['id']+':metric' and edge['relation']=='hasMetric' for edge in edges))
        evidence_ids={node_id for node_id,node in nodes.items() if node['type']=='EvidenceItem'}
        self.assertTrue(any(edge['from']==m['id'] and edge['to'] in evidence_ids and edge['relation']=='supportedBy' for edge in edges))
        self.assertTrue(any(edge['from'] in evidence_ids and edge['relation']=='derivedFrom' and nodes[edge['to']]['type']=='SourceDocument' for edge in edges))

    def test_detailed_publication_links_mechanism_and_test_event(self):
        p,m=self.ready()
        ref=m['evidenceRefs'][0]['evidenceId']
        # capabilityRole is model metadata, not a truth decision; retain it
        # only to classify the already source-bound metric as a mechanism.
        with self.store._connect() as con:
            row=con.execute('SELECT metadata_json FROM ontology_attribute_candidate WHERE candidate_id=?',(m['id'],)).fetchone()
            metadata=json.loads(row[0]);metadata['capabilityRole']='mechanism'
            con.execute('UPDATE ontology_attribute_candidate SET metadata_json=? WHERE candidate_id=?',(json.dumps(metadata,ensure_ascii=False),m['id']))
            con.execute('INSERT INTO ontology_event(event_id,event_type,title,subject_entity_ids,evidence_ids,created_at) VALUES(?,?,?,?,?,?)',('test-linked','PublicTestEvent','公开通信测试',json.dumps([self.id]),json.dumps([ref]),'2026-10-13'))
        p=self.profiles.save(self.id)['profile'];m=self.metric(p,'communication-latency','candidate')
        self.accept(p,m);self.publish(p,m)
        detailed=self.store.published_ontology(detailed=True)
        types={o['type'] for o in detailed['objects']}
        relations={r['relation'] for r in detailed['relations']}
        self.assertIn('Technology',types);self.assertIn('TestEvent',types)
        self.assertTrue({'usesTechnology','measuredIn','testsEquipment'} <= relations)

    def test_rejection_after_acceptance_prevents_publication(self):
        p,m=self.ready();self.accept(p,m);self.accept(p,m,'reject')
        with self.assertRaises(IntelligenceConflict):self.publish(p,m)

    def test_workbook_only_and_unknown_cannot_be_published(self):
        p=self.profiles.save(self.id)['profile']
        for key in ('endurance','communication-latency'):
            m=self.metric(p,key)
            with self.assertRaises(IntelligenceValidationError):self.accept(p,m)

    def test_conflicting_sources_keep_both_values_and_block_publication(self):
        second=BODY.replace('10 ms','20 ms')
        self.agent.queue.provider=Search([BODY,second]);self.agent.trend_provider=Trend(Claims([('10 ms',BODY),('20 ms',second)]))
        self.collect_extract();p=self.profiles.save(self.id)['profile']
        records=[m for c in p['capabilities'] for m in c['metrics'] if m['attributeKey']=='communication-latency']
        self.assertEqual(len(records),2);self.assertTrue(all(m['claimState']=='conflict' for m in records))
        with self.assertRaises(IntelligenceConflict):self.accept(p,records[0])

    def conflicting_profile(self):
        second=BODY.replace('10 ms','20 ms')
        self.agent.queue.provider=Search([BODY,second]);self.agent.trend_provider=Trend(Claims([('10 ms',BODY),('20 ms',second)]))
        self.collect_extract();p=self.profiles.save(self.id)['profile']
        return p,[m for c in p['capabilities'] for m in c['metrics'] if m['attributeKey']=='communication-latency']

    def resolve(self,p,records,winner):
        return self.profiles.review(p['profileId'],{'recordId':winner['id'],'action':'accept','reviewer':'test reviewer','reason':'Checked subject, configuration and original evidence','verifiedConfidence':'high','resolution':{'competingRecordIds':[m['id'] for m in records],'reason':'Fixture reviewer selected the first report after comparing both quotes'}})

    def test_conflict_resolution_keeps_history_and_recomputes_chart_eligibility(self):
        p,records=self.conflicting_profile();winner=records[0];self.resolve(p,records,winner);published=self.publish(p,winner)
        current=self.profiles.read(self.id);after=self.profiles._record(current,winner['id']);loser=self.profiles._record(current,records[1]['id'])
        self.assertEqual(after['claimState'],'observed');self.assertTrue(after['comparability']['eligible']);self.assertEqual(loser['claimState'],'rejected');self.assertFalse(loser['comparability']['eligible'])
        self.assertEqual(current['quality']['conflictCount'],0);self.assertEqual(current['quality']['historicalRejectedCount'],1);self.assertEqual(validate_profile(current),[])
        self.assertEqual(self.store.published_ontology()['admissionCount'],1);self.assertIn('resolution_json',published['publication']['reviews'][0])

    def test_resolution_binds_all_competing_source_content_not_just_ids(self):
        p,records=self.conflicting_profile();self.resolve(p,records,records[0])
        with self.store._connect() as con:con.execute('UPDATE ontology_attribute_candidate SET proposed_value=? WHERE candidate_id=?',(json.dumps('21 ms'),records[1]['id']))
        with self.assertRaises(IntelligenceConflict):self.publish(p,records[0])

    def test_conflicting_winners_cannot_be_published_together(self):
        p,records=self.conflicting_profile();self.resolve(p,records,records[0]);self.resolve(p,records,records[1])
        with self.assertRaises(IntelligenceConflict):self.profiles.publish(p['profileId'],{'recordIds':[m['id'] for m in records],'publishedBy':'reviewer','reason':'must not choose both'})

    def test_new_source_reopens_previously_published_conflict_resolution(self):
        p,records=self.conflicting_profile();self.resolve(p,records,records[0]);self.publish(p,records[0])
        third=BODY.replace('10 ms','30 ms');self.agent.queue.provider=Search([third]);self.agent.trend_provider=Trend(Claims([('30 ms',third)]))
        job=next(j for j in self.agent.queue.plan(self.id,force=True,target_types={'systems'}) if j['targetType']=='systems')
        self.agent.queue.execute(job['verificationId']);self.agent.extract_attribute_candidates(self.id,{'scope':'systems'})
        current=self.profiles.read(self.id)
        self.assertEqual(current['quality']['conflictCount'],3);self.assertEqual(current['quality']['verifiedCount'],0)
        layer=self.store.published_ontology();observation=next(o for o in layer['objects'] if o['type']=='CapabilityObservation')
        self.assertFalse(observation['validity']['isCurrentVerified']);self.assertIsNone(observation['verifiedConfidence'])
        self.assertEqual(layer['provenance'][0]['validity']['currentVerificationStatus'],'conflict')
        with self.assertRaises(IntelligenceConflict):self.publish(p,records[0])

    def test_history_is_version_bound_and_retains_original_state(self):
        p,m=self.ready();self.accept(p,m);self.publish(p,m);latest=self.profiles.save(self.id)['profile']
        history=self.profiles.history(self.id,'communication-latency')
        self.assertEqual([s['profileId'] for s in history['snapshots']],[latest['profileId'],p['profileId']])
        self.assertEqual(history['snapshots'][0]['records'][0]['verificationStatus'],'verified')
        self.assertEqual(history['snapshots'][1]['records'][0]['verificationStatus'],'unverified')
        self.assertEqual(history['snapshots'][1]['contentHash'],p['contentHash'])

    def test_recollection_retains_candidate_source_snapshot(self):
        p,m=self.ready();self.agent.queue.provider=Search([])
        job=next(j for j in self.agent.queue.plan(self.id,force=True,target_types={'systems'}) if j['targetType']=='systems')
        self.agent.queue.execute(job['verificationId'])
        self.assertTrue(self.metric(self.profiles.read(self.id),'communication-latency','candidate')['evidenceRefs'][0]['snapshotMatched'])
        self.accept(p,m);self.assertTrue(self.publish(p,m)['ok'])

    def test_source_ownership_and_literal_value_gates(self):
        self.agent.trend_provider=Trend(Claims([('99 ms',BODY),('10 ms','Not present in any collected body')]))
        result=self.collect_extract();self.assertEqual(result['status'],'no-candidates');self.assertEqual(len(result['rejectedClaims']),2)

    def test_neighbor_variant_cannot_supply_target_properties(self):
        claim={'sourceSubject':'Test UAV Block B','subject':'Test UAV','quote':BODY}
        entity,_=self.agent._entity_document(self.id)
        self.assertFalse(self.agent._claim_targets_entity(claim,entity,[]))

    def test_new_conflict_blocks_an_already_accepted_snapshot(self):
        p,m=self.ready();self.accept(p,m)
        second=BODY.replace('10 ms','20 ms')
        self.agent.queue.provider=Search([second]);self.agent.trend_provider=Trend(Claims([('20 ms',second)]))
        job=next(j for j in self.agent.queue.plan(self.id,force=True,target_types={'systems'}) if j['targetType']=='systems')
        self.agent.queue.execute(job['verificationId']);self.agent.extract_attribute_candidates(self.id,{'scope':'systems'})
        with self.assertRaises(IntelligenceConflict):self.publish(p,m)

    def test_excel_normalizer_preserves_range_and_approximation(self):
        from .ontology_workbook_import import value_type
        registry={'fields':{'endurance':{'valueType':'quantity','unit':'h'}}}
        self.assertEqual(value_type('endurance','90–120 min',registry),('range',{'lower':1.5,'upper':2.0},'h'))
        self.assertEqual(value_type('endurance','约25 h',registry),('quantity',25.0,'h'))

    def test_successful_empty_extraction_differs_from_model_failure(self):
        provider=Claims();provider.extract=lambda document:[];self.agent.trend_provider=Trend(provider)
        self.assertEqual(self.collect_extract()['status'],'no-candidates')
        self.agent.trend_provider=Trend(Claims(fail=True))
        self.assertEqual(self.agent.extract_attribute_candidates(self.id,{'scope':'systems'})['status'],'extraction-failed')

    def test_full_automatic_chain_reports_review_without_publishing(self):
        workflow=CapabilityWorkflow(self.agent)
        run=workflow.submit({'capabilityCodes':['communications'],'maxJobs':1,'maxModelCalls':1},self.id,background=False)
        self.assertEqual(run['status'],'awaiting-human-review');self.assertEqual(run['result']['modelCalls'],1)
        self.assertTrue(all(e['createdAt'] for e in run['events']))
        self.assertEqual(self.store.published_ontology()['admissionCount'],0)
        p=self.profiles.get(run['result']['profileId']);m=self.metric(p,'communication-latency','candidate')
        self.accept(p,m);self.publish(p,m);self.assertEqual(self.profiles.publications(self.id)[0]['publication']['records'][0]['value'],10)

    def test_successful_budgeted_retry_clears_final_failure_but_keeps_trace(self):
        provider=Claims();original=provider.extract;attempts=[]
        def flaky(document):
            attempts.append(1)
            if len(attempts)==1:raise TimeoutError('fixture provider timeout')
            return original(document)
        provider.extract=flaky;self.agent.trend_provider=Trend(provider)
        run=CapabilityWorkflow(self.agent).submit({'capabilityCodes':['communications'],'maxJobs':1,'maxModelCalls':2},self.id,background=False)
        self.assertEqual(run['status'],'awaiting-human-review');self.assertEqual(run['result']['modelCalls'],2)
        self.assertEqual(run['result']['unresolvedModelErrors'],[])
        self.assertTrue(any(e['status']=='extraction-failed' and e['detail']['errorCode']=='provider-timeout' for e in run['events']))
        self.assertTrue(any(e['status']=='retrying' for e in run['events']))

    def test_event_links_require_saved_evidence_overlap(self):
        p,m=self.ready();ref=m['evidenceRefs'][0]['evidenceId']
        with self.store._connect() as con:
            for event_id,evidence in [('related-event',[ref]),('unrelated-event',[])]:
                con.execute('INSERT INTO ontology_event(event_id,event_type,title,subject_entity_ids,evidence_ids,created_at) VALUES(?,?,?,?,?,?)',(event_id,'PublicTestEvent',event_id,json.dumps([self.id]),json.dumps(evidence),'2026-10-13'))
        graph=self.profiles.read(self.id)['graph']
        linked=[e['to'] for e in graph['edges'] if e['from']==m['id'] and e['relation']=='observedDuringEvent']
        self.assertEqual(linked,['related-event'])
        self.assertTrue(any(n['id']=='unrelated-event' and n['type']=='Event' for n in graph['nodes']))

    def test_missing_failed_and_recovery_states_are_distinct(self):
        workflow=CapabilityWorkflow(self.agent);self.agent.queue.provider=Search([])
        run=workflow.submit({'capabilityCodes':['communications']},self.id,background=False)
        self.assertEqual(run['status'],'completed-with-gaps')
        self.agent.queue.provider=Search(fail=True)
        run=workflow.submit({'capabilityCodes':['communications'],'force':True},self.id,background=False)
        self.assertEqual(run['status'],'failed')
        with patch('demo.capability_workflow.threading.Thread'):
            queued=workflow.submit({'capabilityCodes':['communications']},self.id)
        self.assertEqual(workflow.recover(),1);self.assertEqual(workflow.get(queued['runId'])['status'],'interrupted')

    def test_revision_change_blocks_review_and_publication(self):
        p,m=self.ready()
        with self.store._connect() as con:con.execute('UPDATE ontology_entity SET current_revision=current_revision+1 WHERE entity_id=?',(self.id,))
        with self.assertRaises(IntelligenceConflict):self.accept(p,m)
        with self.assertRaises(IntelligenceConflict):self.publish(p,m)

    def test_unknown_cannot_be_filled_with_zero_and_range_cannot_be_exact(self):
        p=self.profiles.read(self.id);m=self.metric(p,'communication-latency');m['value']=0
        p['contentHash']=profile_hash(p);p['profileId']='cap-profile-'+p['contentHash'][:24]
        self.assertTrue(any('unknown cannot' in e for e in validate_profile(p)))


if __name__=='__main__':unittest.main()
