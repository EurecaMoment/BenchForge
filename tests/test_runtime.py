import json
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from benchforge_core.runtime import Runtime, write, jsonl, read, rows, select


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = Runtime(self.root)

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def evidence(self, kind='official'):
        write(self.root/'raw.json', {'answer': 'left'})
        jsonl(self.root/'input.jsonl', [{'id':'e1','media':[],
            'facts':{'answer':'forged'},'selectors':{'answer':'/answer'},
            'provenance':{'kind':kind,'path':'raw.json'}}])
        return self.runtime.call('evidence', {'input':str(self.root/'input.jsonl')})

    def build(self, evidence):
        jsonl(self.root/'items.jsonl',[{'id':'q1','question':'Where is it?',
            'evidence_id':'e1','answer_field':'answer','answer':'forged','metadata':{'secret':1}}])
        return self.runtime.call('build',{'evidence':evidence['evidence'],'items':str(self.root/'items.jsonl')})

    def test_source_replay_and_public_authority_separation(self):
        evidence=self.evidence()
        self.assertEqual(rows(evidence['evidence'])[0]['facts']['answer'],'left')
        package=self.build(evidence)
        self.assertEqual(rows(Path(package['authority'])/'gold.jsonl')[0]['answer'],'left')
        with zipfile.ZipFile(package['package']) as archive:
            self.assertEqual(archive.namelist(), ['media/', 'items.jsonl'])
            item=json.loads(archive.read('items.jsonl'))
            self.assertEqual(set(item), {'id','question','media'})

    def test_prediction_not_promoted_and_failure_is_durable(self):
        with self.assertRaisesRegex(ValueError,'Prediction-only'):
            self.build(self.evidence('prediction'))
        self.assertEqual(self.runtime.call('status',{})['operations'][0]['state'],'failed')

    def test_evaluation_missing_and_duplicate_ids(self):
        package=self.build(self.evidence())
        jsonl(self.root/'predictions.jsonl',[])
        request={'authority':package['authority'],'predictions':str(self.root/'predictions.jsonl')}
        score=self.runtime.call('evaluate',request)
        self.assertEqual((score['missing'],score['accuracy']),(1,0))
        jsonl(self.root/'predictions.jsonl',[{'id':'q1','answer':'left'}]*2)
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            self.runtime.call('evaluate',request)

    def test_pointer_escapes_and_empty_key(self):
        self.assertEqual(select({'a/b':{'':2}},'/a~1b/'),2)

    def test_missing_backend_has_setup_guidance(self):
        with self.assertRaisesRegex(ValueError, 'Configure services.sam3.url'):
            self.runtime.call('sam3', {'payload':{}})

    def test_http_contract_without_starting_services(self):
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append((self.path,json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"masks":[]}')
            def log_message(self,*args):
                pass
        server=HTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            self.runtime.config={'services':{'sam3':{'url':f'http://127.0.0.1:{server.server_port}'}}}
            result=self.runtime.call('sam3',{'payload':{'image_path':'image.png','text_prompt':'chair'}})
            self.assertEqual(requests,[('/image/infer',{'image_path':'image.png','text_prompt':'chair'})])
            self.assertFalse(result['gt_written'])
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


if __name__=='__main__':
    unittest.main()
