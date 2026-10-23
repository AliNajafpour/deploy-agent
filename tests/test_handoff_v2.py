import json
import unittest

from lyara.handoff import MAX_OUTPUT,coding_handoff,handoff_markdown


class HandoffChecks(unittest.TestCase):
    def test_redacts_secrets_and_exports_only_valid_source_locations(self):
        state={'id':'run','request':{'message':'Repair'},'error':'app.py:2 private-value C:\\private\\file',
               'artifact':{'sha256':'a'*64},'context':{'deployment_files':{
                   'app.py':{'content':'one\ntwo\nthree','sha256':'b'*64},
                   '../private.py':{'content':'hidden'}},'observations':{'logs':{
                       'data':['app.py:999 private-value','app.py:2 failure']}}}}
        result=coding_handoff(state,secrets=('private-value',))
        self.assertEqual([(x['path'],x['line']) for x in result['locations']],[('app.py',2)])
        rendered=handoff_markdown(result)
        self.assertNotIn('private-value',rendered)
        self.assertNotIn('C:\\private',rendered)
        self.assertIn('acceptance_checks',result)
        self.assertLessEqual(len(json.dumps(result,ensure_ascii=False).encode()),MAX_OUTPUT)

    def test_missing_evidence_has_no_invented_location_or_hash(self):
        result=coding_handoff({})
        self.assertEqual(result['locations'],[])
        self.assertIsNone(result['source']['sha256'])
        self.assertTrue(result['limitations'])


if __name__=='__main__':unittest.main()
