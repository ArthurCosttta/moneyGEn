"""v2: local manifests, output routing, settings and import HTTP boundaries."""
import base64
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from test_flowlocal import FakeCLI, PNG, Engine, AppError, Server
from manifests import ManifestError, normalize_manifest, match_asset


class BatchImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cli = FakeCLI(self.root)
        self.e = Engine(self.root, self.cli)
        self.images = self.root / 'campanha'
        self.images.mkdir()
        for name in ('sérum.png', 'chaleira.png'):
            (self.images / name).write_bytes(PNG)
        self.prompt = '  --Câmera "ação"\n$HOME & texto intacto.  '

    def tearDown(self):
        if self.e.worker:
            self.e.worker.join(10)
        self.tmp.cleanup()

    def document(self, **settings):
        return {'model':'omni-1.2', **settings, 'takes':[
            {'image':'sérum.png','prompt':self.prompt,'video':'take-01.mp4'},
            {'image':'chaleira.png','prompt':'Outro prompt.','video':'take-02.mp4'}]}

    def imported(self, **settings):
        return self.e.import_documents([{'document':self.document(**settings),'manifest_dir':str(self.images)}])

    def finish_test(self, batch):
        self.e.begin('test',batch['id'],True,True)
        self.e.worker.join(10)
        self.assertFalse(self.e.busy)

    def test_relative_images_from_native_json_directory_and_exact_prompt(self):
        imported=self.imported()
        self.assertEqual(len(imported['items']),2)
        self.assertEqual(imported['items'][0]['prompt'],self.prompt)
        self.assertEqual(self.cli.calls,[])

    def test_browser_json_matches_selected_images_and_does_not_read_absolute_path(self):
        asset=self.e.stage('sérum.png',base64.b64encode(PNG).decode())
        result=self.e.import_documents([{'document':[{'image':r'C:\Fotos\sérum.png','prompt':self.prompt}]}],assets=[asset])
        self.assertEqual(result['items'][0]['id'],asset['id'])
        self.assertEqual(result['items'][0]['prompt'],self.prompt)

    def test_relative_images_require_folder_when_browser_hides_parent(self):
        with self.assertRaisesRegex(AppError,'Falta localizar'):
            self.e.import_documents([{'document':self.document()}])
        self.assertEqual(self.cli.calls,[])
        self.assertEqual(list((self.e.data/'imagens').glob('*.png')),[])

    def test_picked_image_base_resolves_json_and_prevents_escape(self):
        result=self.e.import_documents([{'document':self.document()}],images_dir=str(self.images))
        self.assertEqual(len(result['items']),2)
        outside=self.root/'outside.png';outside.write_bytes(PNG)
        with self.assertRaisesRegex(AppError,'fora da pasta'):
            self.e.import_documents([{'document':[{'image':'../outside.png','prompt':'x'}]}],images_dir=str(self.images))

    def test_absolute_local_images_need_no_folder(self):
        result=self.e.import_documents([{'document':[{'image':str(self.images/'sérum.png'),'prompt':self.prompt}]}])
        self.assertEqual(result['items'][0]['name'],'sérum.png')

    def test_links_network_and_non_images_are_rejected(self):
        for value in ('https://example.com/secret.png',r'\\server\share\image.png','data:image/png;base64,abc'):
            with self.assertRaises((AppError,ManifestError,OSError)):
                self.e.import_documents([{'document':[{'image':value,'prompt':'x'}]}])
        (self.images/'not-image.png').write_text('private text')
        with self.assertRaises(AppError):
            self.e.import_documents([{'document':[{'image':str(self.images/'not-image.png'),'prompt':'x'}]}])

    def test_duplicate_basename_is_not_guessed_and_relative_match_disambiguates(self):
        assets=[{'id':'1','name':'same.png','relative_path':'folder/a/same.png'},{'id':'2','name':'same.png','relative_path':'folder/b/same.png'}]
        with self.assertRaises(ManifestError):match_asset('same.png',assets)
        self.assertEqual(match_asset('b/same.png',assets)['id'],'2')

    def test_multiple_manifests_preserve_order_and_reject_conflicting_settings(self):
        documents=[{'name':'a.json','document':{'takes':[{'image':str(self.images/'sérum.png'),'prompt':'1'}],'model':'omni-1.2'}}, {'name':'b.json','document':{'takes':[{'image':str(self.images/'chaleira.png'),'prompt':'2'}],'model':'omni-1.2'}}]
        self.assertEqual([r['prompt'] for r in self.e.import_documents(documents)['items']],['1','2'])
        documents[1]['document']['model']='veo-lite'
        with self.assertRaisesRegex(AppError,'configurações diferentes'):self.e.import_documents(documents)

    def test_common_json_fields_and_model_labels(self):
        rows,settings=normalize_manifest({'model':'Omni 1.2 Flash','duration':'auto','prompts':[{'ref':'sérum.png','text':self.prompt}]})
        self.assertEqual(rows[0]['prompt'],self.prompt)
        self.assertIsNone(settings['duration'])
        self.assertEqual(settings['model'],'omni-1.2')

    def test_repeated_selected_image_creates_distinct_take_ids(self):
        asset=self.e.stage('sérum.png',base64.b64encode(PNG).decode())
        result=self.e.import_documents([{'document':[{'image':'sérum.png','prompt':'1'},{'image':'sérum.png','prompt':'2'}]}],assets=[asset])
        self.assertNotEqual(result['items'][0]['id'],result['items'][1]['id'])
        self.e.plan(result['items'],'omni-1.2')

    def test_exact_output_folder_receives_videos_not_extra_batch_folder(self):
        imported=self.imported();destination=self.root/'meus-videos'
        batch=self.e.plan(imported['items'],'omni-1.2',output_dir=str(destination))
        self.finish_test(batch)
        self.assertTrue((destination/'take-01.mp4').is_file())
        self.assertFalse((destination/batch['id']).exists())
        self.assertTrue((Path(batch['report_dir'])/'relatorio.json').is_file())
        self.assertEqual(self.e.export_path(batch['id'],'take-01.mp4'),destination/'take-01.mp4')
        self.e.begin('batch',batch['id'],True,True,True);self.e.worker.join(10)
        self.assertTrue((destination/'take-02.mp4').is_file())

    def test_existing_file_and_duplicate_names_never_overwritten(self):
        imported=self.imported();destination=self.root/'saida-escolhida';destination.mkdir()
        (destination/'take-01.mp4').write_bytes(b'keep me')
        with self.assertRaisesRegex(AppError,'já existe'):
            self.e.plan(imported['items'],'omni-1.2',output_dir=str(destination))
        self.assertEqual((destination/'take-01.mp4').read_bytes(),b'keep me')
        imported['items'][1]['output_name']='TAKE-01.mp4'
        with self.assertRaises(AppError):self.e.plan(imported['items'],'omni-1.2',output_dir=str(self.root/'other'))

    def test_file_appearing_after_plan_is_not_overwritten_or_billed(self):
        imported=self.imported();destination=self.root/'videos'
        batch=self.e.plan(imported['items'],'omni-1.2',output_dir=str(destination))
        (destination/'take-01.mp4').write_bytes(b'keep')
        self.finish_test(batch)
        self.assertFalse(any(c[:2]==['video','i2v'] for c in self.cli.calls))
        self.assertEqual((destination/'take-01.mp4').read_bytes(),b'keep')

    def test_output_preference_survives_restart_and_old_download_stays_routed(self):
        imported=self.imported();destination=self.root/'videos'
        batch=self.e.plan(imported['items'],'omni-1.2',output_dir=str(destination));self.finish_test(batch)
        recovered=Engine(self.root,self.cli)
        self.assertEqual(recovered.output,destination)
        self.assertEqual(recovered.export_path(batch['id'],'take-01.mp4'),destination/'take-01.mp4')
        for i in imported['items']:i['output_name']=None
        recovered.plan(imported['items'],'omni-1.2',output_dir=str(self.root/'another'))
        self.assertEqual(recovered.export_path(batch['id'],'take-01.mp4'),destination/'take-01.mp4')

    def test_duration_aspect_resolution_reach_video_command(self):
        imported=self.imported()
        batch=self.e.plan(imported['items'],'omni-1.2',aspect='16:9',duration=10,resolution='720p')
        self.finish_test(batch)
        call=next(c for c in self.cli.calls if c[:2]==['video','i2v'])
        for flag,value in [('--aspect','16:9'),('--duration','10'),('--resolution','720p')]:self.assertEqual(call[call.index(flag)+1],value)

    def test_auto_duration_is_omitted_and_invalid_model_controls_rejected(self):
        imported=self.imported();batch=self.e.plan(imported['items'],'veo-lite',duration=None)
        self.finish_test(batch)
        call=next(c for c in self.cli.calls if c[:2]==['video','i2v'])
        self.assertNotIn('--duration',call)
        self.assertNotIn('--resolution',call)
        for options in ({'duration':10},{'resolution':'720p'},{'aspect':'1:1'},{'duration':True}):
            with self.assertRaises(AppError):self.e.plan(imported['items'],'veo-lite',**options)

    def test_unsafe_output_filenames_rejected(self):
        for name in ('../secret.mp4',r'C:\secret.mp4','CON.mp4','x.txt','x?.mp4','a/b.mp4'):
            with self.assertRaises(AppError):self.e.validate_video_name(name)

    def test_native_manifest_images_dir_is_relative_to_json_parent(self):
        document=self.document(images_dir='campanha')
        result=self.e.import_documents([{'document':document,'manifest_dir':str(self.root)}])
        self.assertEqual(len(result['items']),2)

    def test_preflight_checks_metadata_account_and_output_without_generation(self):
        original=self.cli.call
        def readonly(args,timeout=1800):
            if args==['--version']:return 0,'gflow, version 0.103.1',''
            if args[:2]==['models','show']:return 0,json.dumps({'durations':[4,6,8]}),''
            return original(args,timeout)
        self.cli.call=readonly
        self.e.preflight({'model':'veo-lite','duration':6,'output_dir':str(self.root/'destination')})
        self.assertTrue(self.e.state['preflight']['ready'])
        self.assertFalse(any(c[0] in ('image','video') for c in self.cli.calls))

    def test_preflight_invalid_configuration_replaces_old_ready_status(self):
        self.e.state['preflight']={'ready':True}
        self.e.preflight({'model':'veo-lite','duration':15})
        self.assertFalse(self.e.state['preflight']['ready'])
        self.assertIn('Duração',self.e.state['preflight']['error'])

    def test_new_plan_keeps_requested_name_separate_from_generated_name(self):
        imported=self.imported()
        for row in imported['items']:row['output_name']=None
        batch=self.e.plan(imported['items'],'omni-1.2')
        self.assertIsNone(batch['rows'][0]['requested_output_name'])
        self.assertTrue(batch['rows'][0]['output_name'].endswith('.mp4'))


class ImportHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.e=Engine(self.root,FakeCLI(self.root));self.server=Server(('127.0.0.1',0),self.e)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()
    def post(self,path,body):
        request=Request(self.server.origin+'/api/'+path,json.dumps(body).encode(),{'X-Flow-Token':self.server.token,'Content-Type':'application/json'})
        with urlopen(request,timeout=10) as response:return json.load(response)
    def test_browser_cannot_supply_trusted_manifest_parent(self):
        file=self.root/'test.png';file.write_bytes(PNG)
        with self.assertRaises(HTTPError):self.post('import-manifest',{'documents':[{'document':[{'image':'test.png','prompt':'x'}],'manifest_dir':str(self.root)}]})
        self.assertEqual(self.e.cli.calls,[])
    def test_native_json_picker_resolves_relative_images(self):
        (self.root/'test.png').write_bytes(PNG)
        file=self.root/'lote.json';file.write_text(json.dumps({'takes':[{'image':'test.png','prompt':'  exato  '}]}))
        with patch('app.choose',return_value=[str(file)]):result=self.post('import-native',{})
        self.assertEqual(result['items'][0]['prompt'],'  exato  ')
        self.assertEqual(self.e.cli.calls,[])
    def test_native_folder_cancel_does_not_change_output(self):
        with patch('app.choose',return_value=[]):result=self.post('choose-folder',{})
        self.assertIsNone(result['path'])
        self.assertEqual(self.e.output,self.root/'saida')
    def test_thumbnail_is_authenticated(self):
        image=self.e.stage('test.png',base64.b64encode(PNG).decode())
        url=self.server.origin+'/media/image/'+image['id']
        with self.assertRaises(HTTPError):urlopen(url,timeout=5)
        with urlopen(Request(url,headers={'X-Flow-Token':self.server.token}),timeout=5) as response:self.assertEqual(response.read(),PNG)

if __name__=='__main__':unittest.main(verbosity=2)
