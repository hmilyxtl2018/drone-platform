import io
import json
import time
import unittest
from unittest.mock import Mock
from .osint_collector import FirecrawlSearchProvider

class CapabilityCollectionTests(unittest.TestCase):
    def test_catalogue_follows_exact_model_page_not_neighbor_variant(self):
        calls=[];opener=Mock()
        def respond(request,**kwargs):
            payload=json.loads(request.data);calls.append(payload)
            if request.full_url.endswith('/search'):
                data=[{'title':'Remotely Piloted Aircraft | GA-ASI','url':'https://www.ga-asi.com/remotely-piloted-aircraft/','markdown':'[Gray Eagle ER](https://www.ga-asi.com/remotely-piloted-aircraft/gray-eagle-er)\n[Gray Eagle](https://www.ga-asi.com/remotely-piloted-aircraft/gray-eagle)'}]
            else:
                data={'markdown':'Gray Eagle endurance is 25 hours.','metadata':{'title':'Gray Eagle'}}
            return io.BytesIO(json.dumps({'success':True,'data':data}).encode())
        opener.open.side_effect=respond
        provider=FirecrawlSearchProvider(api_key='fixture-key',opener=opener)
        result=provider.search('Gray Eagle specifications',['"Gray Eagle"'],since_days=0,max_items=1,source_ids=['manufacturer-disclosure'])
        self.assertEqual(calls[1]['url'],'https://www.ga-asi.com/remotely-piloted-aircraft/gray-eagle')
        self.assertEqual(result['items'][0]['provenance']['discoveredFrom'],'https://www.ga-asi.com/remotely-piloted-aircraft/')
        self.assertEqual(result['items'][0]['body'],'Gray Eagle endurance is 25 hours.')
    def test_expired_collection_budget_does_not_call_provider(self):
        opener=Mock();provider=FirecrawlSearchProvider(api_key='fixture-key',opener=opener)
        result=provider.search('equipment',['"Test UAV"'],since_days=0,max_items=1,source_ids=['manufacturer-disclosure'],deadline_monotonic=time.monotonic()-1)
        self.assertEqual(result['budget']['stopReason'],'time-budget-exhausted');opener.open.assert_not_called()

if __name__=='__main__':unittest.main()
