"""HTTP admission regression; isolated store and mocked provider boundaries."""
from contextlib import ExitStack
import importlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request,urlopen

from .intelligence_agent import IntelligenceAgent,DeterministicClaimProvider
from .intelligence_store import IntelligenceStore
from .ontology_workbook_import import import_workbook
from .ontology_agent import OntologyAgent
from .equipment_verification import EquipmentVerificationQueue
from .test_ontology_agent import fixture_workbook
from .test_capability_profile import Search,Trend,Claims,BODY


class CapabilityAPITests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        workbook=root/'test.xlsx';self.db=root/'api.sqlite';fixture_workbook(workbook);import_workbook(workbook,self.db)
        self.store=IntelligenceStore(self.db)
        agent=IntelligenceAgent(self.store,provider=DeterministicClaimProvider(),trend_provider=Trend())
        with patch('demo.provider_secrets.load_provider_environment'),patch('demo.intelligence_agent.IntelligenceAgent',return_value=agent),patch('demo.runtime_store.RuntimeStore',return_value=object()),patch('demo.osint_store.OSINTTaskStore',return_value=object()):
            self.serve=importlib.import_module('demo.serve')
        self.object_agent=OntologyAgent(self.store,trend_provider=Trend());self.object_agent.queue=EquipmentVerificationQueue(self.store,Search())
        self.stack.enter_context(patch.object(self.serve,'INTELLIGENCE_AGENT',agent))
        self.stack.enter_context(patch.object(self.serve,'ontology_agent',return_value=self.object_agent))
        self.stack.enter_context(patch.object(self.serve.Handler,'log_message',lambda *args:None))
        self.server=self.serve.ThreadingHTTPServer(('127.0.0.1',0),self.serve.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_address[1]}'
        self.id=next(e['entityId'] for e in self.object_agent._entities() if e['preferredName']=='Test UAV')
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join(3);self.stack.close()
    def request(self,path,body=None):
        req=Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'},method='POST' if body is not None else 'GET')
        try:
            with urlopen(req,timeout=10) as response:return response.status,json.load(response)
        except HTTPError as e:
            with e:return e.code,json.load(e)
    def test_read_run_review_publication_and_ontology_layer(self):
        root=f'/api/ontology/entities/{self.id}'
        code,read=self.request(root+'/capability-profile');self.assertEqual(code,200);self.assertEqual(read['profile']['objectRevision'],1)
        # Execute synchronously in the HTTP test, preserving the production API's 202 contract.
        from .capability_workflow import CapabilityWorkflow
        original=CapabilityWorkflow.submit
        with patch.object(CapabilityWorkflow,'submit',lambda obj,payload,entity_id:original(obj,payload,entity_id,background=False)):
            code,result=self.request(root+'/capability-runs',{'capabilityCodes':['communications']})
        self.assertEqual(code,202);self.assertEqual(result['run']['status'],'awaiting-human-review')
        pid=result['run']['result']['profileId']
        code,result=self.request('/api/ontology/capability-profiles/'+pid);self.assertEqual(code,200)
        record=next(m for c in result['profile']['capabilities'] for m in c['metrics'] if m['attributeKey']=='communication-latency' and m['origin']=='candidate')
        publish={'recordIds':[record['id']],'publishedBy':'tester','reason':'literal verification'}
        code,_=self.request(f'/api/ontology/capability-profiles/{pid}/publish',publish);self.assertEqual(code,409)
        code,_=self.request(f'/api/ontology/capability-profiles/{pid}/review',{'recordId':record['id'],'action':'accept','reviewer':'tester','reason':'literal evidence checked','verifiedConfidence':'high'});self.assertEqual(code,200)
        code,result=self.request(f'/api/ontology/capability-profiles/{pid}/publish',publish);self.assertEqual(code,200)
        code,result=self.request(root+'/capability-publications');self.assertEqual(code,200);self.assertEqual(len(result['publications']),1)
        code,result=self.request(root+'/capability-profile');self.assertEqual(result['profile']['quality']['verifiedCount'],1)
        code,result=self.request(f'/api/ontology/capability-compare?entityIds={self.id}&attributeKey=communication-latency');self.assertEqual(code,200);self.assertEqual(result['comparison']['cohorts'][0]['sampleSize'],1)
    def test_missing_entity_unknown_field_and_invalid_review(self):
        self.assertEqual(self.request('/api/ontology/entities/no-such-entity/capability-profile')[0],404)
        self.assertEqual(self.request(f'/api/ontology/capability-compare?entityIds={self.id}&attributeKey=made-up')[0],400)
        self.assertEqual(self.request(f'/api/ontology/entities/{self.id}/capability-runs',{'capabilityCodes':['made-up']})[0],400)

    def test_manufacturing_research_routes_keep_collection_and_extraction_separate(self):
        root=f'/api/ontology/entities/{self.id}/manufacturing-research'
        code,status=self.request(root);self.assertEqual(code,200);self.assertIsNone(status['verification'])
        code,collected=self.request(root+'/collect',{'force':True});self.assertEqual(code,200)
        self.assertEqual(collected['status'],'needs-review');self.assertEqual(collected['retainedItems'],1)
        code,extract=self.request(root+'/extract',{});self.assertEqual(code,200)
        self.assertEqual(extract['status'],'model-unavailable');self.assertEqual(extract['candidateCount'],0)
        code,manufacturing=self.request(f'/api/ontology/entities/{self.id}/manufacturing')
        self.assertEqual(code,200);self.assertEqual(manufacturing['candidates'],[])

    def test_conflict_review_and_publication_reads_selected_value_and_history(self):
        second=BODY.replace('10 ms','20 ms')
        self.object_agent.queue.provider=Search([BODY,second]);self.object_agent.trend_provider=Trend(Claims([('10 ms',BODY),('20 ms',second)]))
        job=next(j for j in self.object_agent.queue.plan(self.id) if j['targetType']=='systems')
        self.object_agent.queue.execute(job['verificationId']);self.object_agent.extract_attribute_candidates(self.id,{'scope':'systems'})
        root=f'/api/ontology/entities/{self.id}';code,data=self.request(root+'/capability-profile',{});self.assertEqual(code,201)
        p=data['profile'];records=[m for c in p['capabilities'] for m in c['metrics'] if m['attributeKey']=='communication-latency']
        review={'recordId':records[0]['id'],'action':'accept','reviewer':'tester','reason':'Compared both sources','verifiedConfidence':'high'}
        endpoint=f"/api/ontology/capability-profiles/{p['profileId']}"
        self.assertEqual(self.request(endpoint+'/review',review)[0],409)
        review['resolution']={'competingRecordIds':[m['id'] for m in records],'reason':'Explicit source-selection decision in test'}
        self.assertEqual(self.request(endpoint+'/review',review)[0],200)
        self.assertEqual(self.request(endpoint+'/publish',{'recordIds':[records[0]['id']],'publishedBy':'tester','reason':'Publish the reviewed source value'})[0],200)
        code,data=self.request(root+'/capability-profile');self.assertEqual(code,200)
        self.assertEqual(data['profile']['quality']['verifiedCount'],1);self.assertEqual(data['profile']['quality']['historicalRejectedCount'],1)
        code,history=self.request(root+'/capability-profile-history?attributeKey=communication-latency');self.assertEqual(code,200)
        self.assertEqual(history['history']['snapshots'][0]['profileId'],p['profileId'])
        self.assertEqual(len(history['history']['snapshots'][0]['records']),2)


if __name__=='__main__':unittest.main()
