"""Offline safety and orchestration tests. FakeCLI NEVER calls Google."""
import base64
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Server
from engine import ACCOUNT, AppError, CLI, Engine, parse_json

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j2ioAAAAASUVORK5CYII=')
MP4 = b'\x00\x00\x00\x18ftypisom' + b'\x00' * 64  # file-validation fixture, not a real video
REFUSAL = 'PUBLIC_ERROR_UNSAFE_GENERATION: exact refusal\nLinha 2 — não modificar.'


class FakeCLI:
    def __init__(self, root, refuse=None, fail_upload=None, video=None):
        self.executable = Path(root) / 'fake-executable'
        self.executable.touch()
        self.calls = []
        self.balance = 1000
        self.refuse = refuse or set()
        self.fail_upload = fail_upload or set()
        self.video = video or MP4
        self.credits_fail = False

    def credits(self):
        self.calls.append(['credits'])
        if self.credits_fail:
            raise AppError('Conta não confirmada')
        return {'credits': self.balance, 'email': ACCOUNT, 'checked_at': '2026-11-21T12:00:00Z'}

    def call(self, args, timeout=1800):
        self.calls.append(list(args))
        if args[:2] == ['image', 'upload']:
            index = len([c for c in self.calls if c[:2] == ['image', 'upload']])
            if index in self.fail_upload:
                return 1, '', 'EXACT_UPLOAD_REFUSAL'
            return 0, f'Creating project...\n  Project: project-{index}\n  Asset UUID: 00000000-0000-0000-0000-{index:012d}\nDimensions: 1 x 1 Project: project-{index}\n', ''
        if args[:2] == ['video', 'i2v']:
            index = int(args[args.index('--project')+1].split('-')[-1])
            self.balance -= 20
            if index in self.refuse:
                return 1, json.dumps({'status': 'fail', 'succeeded': False, 'failure_reasons': [REFUSAL], 'charged': 'maybe'}), ''
            target = Path(args[args.index('--out-file')+1])
            target.write_bytes(self.video)
            return 0, json.dumps({'status': 'ok', 'succeeded': True, 'local_path': str(target), 'media_id': str(index)}), ''
        if args[:2] == ['auth', 'login']:
            return 0, 'not saved authentication output', ''
        raise AssertionError(args)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cli = FakeCLI(self.root)
        self.e = Engine(self.root, self.cli)

    def tearDown(self):
        if self.e.worker:
            self.e.worker.join(10)
        self.tmp.cleanup()

    def plan(self, n=3):
        items = []
        for i in range(n):
            image = self.e.stage(f'{i+1}.png', base64.b64encode(PNG).decode())
            items.append({'id': image['id'], 'prompt': f'  --prompt {i+1} "literal"\n$HOME & echo test  '})
        return self.e.plan(items, 'omni-1.2')

    def wait(self):
        self.e.worker.join(10)
        self.assertFalse(self.e.worker.is_alive())
        self.assertFalse(self.e.busy)

    def test_dry_run_no_cli_calls_and_exact_prompts(self):
        batch = self.plan()
        self.assertEqual([], self.cli.calls)
        saved = json.loads((self.e.output/batch['id']/'relatorio.json').read_text())
        self.assertEqual(saved['rows'][0]['prompt'], '  --prompt 1 "literal"\n$HOME & echo test  ')
        self.assertFalse(batch['approvals'])

    def test_all_uploads_before_any_video_and_test_never_repeated(self):
        batch = self.plan()
        self.e.begin('test', batch['id'], True, True)
        self.wait()
        calls = [c for c in self.cli.calls if c[0] in ('image','video')]
        self.assertEqual([c[0] for c in calls], ['image','image','image','video'])
        self.assertEqual(calls[-1][-1], batch['rows'][0]['prompt'])
        self.assertEqual(calls[-1][-2], '--')
        self.assertEqual('6', calls[-1][calls[-1].index('--duration')+1])
        self.assertEqual('1', calls[-1][calls[-1].index('--count')+1])
        self.assertEqual(self.e.state['batch']['phase'], 'review')
        with self.assertRaises(AppError): self.e.begin('batch',batch['id'],True,True,False)
        self.e.begin('batch',batch['id'],True,True,True)
        self.wait()
        videos = [c for c in self.cli.calls if c[:2]==['video','i2v']]
        self.assertEqual(len(videos),3)
        self.assertEqual([c[-1] for c in videos], [r['prompt'] for r in batch['rows']])
        self.assertEqual(self.e.state['batch']['phase'],'completed')
        self.assertTrue(all(r['credits_observed']==20 for r in self.e.state['batch']['rows']))

    def test_refusal_is_exact_and_remaining_takes_continue(self):
        self.cli.refuse={2}
        batch=self.plan()
        self.e.begin('test',batch['id'],True,True); self.wait()
        self.e.begin('batch',batch['id'],True,True,True); self.wait()
        rows=self.e.state['batch']['rows']
        self.assertEqual(rows[1]['error'],REFUSAL)
        self.assertEqual(rows[1]['status'],'recusado_ou_falhou')
        self.assertEqual(rows[2]['status'],'concluido')
        log=(self.e.output/batch['id']/'002-geracao.log').read_text()
        self.assertIn('PUBLIC_ERROR_UNSAFE_GENERATION',log)

    def test_first_refusal_allows_next_test_with_fresh_consent_no_repeat(self):
        self.cli.refuse={1}; batch=self.plan()
        self.e.begin('test',batch['id'],True,True); self.wait()
        self.assertEqual(self.e.state['batch']['phase'],'test_failed')
        with self.assertRaises(AppError): self.e.begin('batch',batch['id'],True,True,True)
        self.assertEqual(len([c for c in self.cli.calls if c[0]=='video']),1)
        with self.assertRaises(AppError): self.e.begin('test',batch['id'],False,True)
        self.e.begin('test',batch['id'],True,True);self.wait()
        self.assertEqual(self.e.state['batch']['test_index'],2)
        self.e.begin('batch',batch['id'],True,True,True);self.wait()
        self.assertEqual(self.e.state['batch']['rows'][2]['status'],'concluido')
        videos=[c for c in self.cli.calls if c[0]=='video']
        self.assertEqual(len(videos),3)
        self.assertEqual(len([c for c in videos if c[c.index('--project')+1]=='project-1']),1)

    def test_missing_confirmation_stale_plan_and_wrong_phase_block(self):
        batch=self.plan()
        for consent in [(False,True),(True,False),(1,True),('true',True)]:
            with self.assertRaises(AppError): self.e.begin('test',batch['id'],*consent)
        with self.assertRaises(AppError): self.e.begin('test','stale',True,True)
        with self.assertRaises(AppError): self.e.begin('batch',batch['id'],True,True,True)
        self.assertEqual(self.cli.calls,[])

    def test_no_spend_is_enforced_on_server(self):
        batch=self.plan();self.e.no_spend=True
        with self.assertRaises(AppError):self.e.begin('test',batch['id'],True,True)
        self.assertEqual(self.cli.calls,[])

    def test_wrong_account_prevents_uploads_and_spending(self):
        batch=self.plan();self.cli.credits_fail=True
        self.e.begin('test',batch['id'],True,True);self.wait()
        self.assertEqual([['credits']],self.cli.calls)
        self.assertEqual(self.e.state['batch']['phase'],'interrupted')

    def test_one_upload_failure_does_not_prevent_other_uploads(self):
        self.cli.fail_upload={2};batch=self.plan()
        self.e.begin('test',batch['id'],True,True);self.wait()
        self.e.begin('batch',batch['id'],True,True,True);self.wait()
        rows=self.e.state['batch']['rows']
        self.assertEqual(rows[1]['status'],'falha_upload')
        self.assertEqual(rows[2]['status'],'concluido')
        self.assertEqual(len([c for c in self.cli.calls if c[0]=='video']),2)

    def test_interrupted_state_never_auto_resumes(self):
        batch=self.plan();self.e.state['batch']['phase']='running'
        self.e.state['batch']['rows'][0]['status']='gerando';self.e.save()
        recovered=Engine(self.root,self.cli)
        self.assertEqual(recovered.state['batch']['phase'],'interrupted')
        self.assertEqual(recovered.state['batch']['rows'][0]['status'],'incerto')
        self.assertEqual(self.cli.calls,[])

    def test_image_tamper_after_plan_prevents_upload(self):
        batch=self.plan(1)
        (self.e.data/'imagens'/batch['rows'][0]['file']).write_bytes(b'bad')
        self.e.begin('test',batch['id'],True,True);self.wait()
        self.assertEqual(self.e.state['batch']['phase'],'test_failed')
        self.assertFalse(any(c[0] in ('image','video') for c in self.cli.calls))

    def test_missing_output_not_claimed_success(self):
        self.cli.video=b'not mp4';batch=self.plan(1)
        self.e.begin('test',batch['id'],True,True);self.wait()
        self.assertEqual(self.e.state['batch']['rows'][0]['status'],'sem_mp4')
        self.assertIsNone(self.e.state['batch']['rows'][0]['path'])

    def test_invalid_uploads_and_traversal_blocked(self):
        for data in [b'hello',b'',b'<script>test</script>']:
            with self.assertRaises(AppError):self.e.stage('fake.png',base64.b64encode(data).decode())
        with self.assertRaises(AppError):self.e.export_path('../secrets','file.log')
        batch=self.plan(1)
        with self.assertRaises(AppError):self.e.export_path(batch['id'],'../estado.json')

    def test_busy_rejects_new_plan(self):
        self.e.busy=True
        with self.assertRaises(AppError):self.e.plan([],'omni-1.2')
        self.e.busy=False

    def test_stop_waits_for_current_and_skips_rest(self):
        batch=self.plan()
        self.e.begin('test',batch['id'],True,True);self.wait()
        original=self.e.generate
        def stop_after(row):
            original(row);self.e.request_stop()
        self.e.generate=stop_after
        self.e.begin('batch',batch['id'],True,True,True);self.wait()
        self.assertEqual(self.e.state['batch']['phase'],'cancelled')
        self.assertEqual(self.e.state['batch']['rows'][2]['status'],'cancelado')
        self.assertEqual(len([c for c in self.cli.calls if c[0]=='video']),2)

    def test_cli_environment_disables_telemetry_cloud_and_mcp_spending(self):
        env=CLI(self.root).env()
        self.assertEqual(env['GFLOW_CLI_TELEMETRY'],'0')
        self.assertEqual(env['GFLOW_CLI_STORAGE_URI'],'')
        self.assertEqual(env['GFLOW_MCP_NO_SPEND'],'1')
        self.assertEqual(env['GFLOW_CLI_HOME'],str(self.root/'dados'/'flow'))

    def test_missing_balance_not_reported_as_zero(self):
        batch=self.plan(1);original=self.cli.credits
        counter=[0]
        def credits():
            counter[0]+=1
            if counter[0]==3:raise AppError('indisponível')
            return original()
        self.cli.credits=credits
        self.e.begin('test',batch['id'],True,True);self.wait()
        self.assertIsNone(self.e.state['batch']['rows'][0]['credits_observed'])

    def test_json_parser_handles_cli_logs_and_nested_fields(self):
        out='{"event":"log"}\n'+json.dumps({'status':'ok','succeeded':True,'request':{'model':'omni'}})+'\n'
        self.assertTrue(parse_json(out)['succeeded'])

    def test_failed_only_test_cannot_be_repeated(self):
        self.cli.refuse={1};batch=self.plan(1)
        self.e.begin('test',batch['id'],True,True);self.wait()
        with self.assertRaises(AppError):self.e.begin('test',batch['id'],True,True)
        self.assertEqual(len([c for c in self.cli.calls if c[0]=='video']),1)

    def test_wrong_account_is_checked_by_real_credit_adapter(self):
        adapter=CLI(self.root)
        adapter.call=lambda *args: (0,json.dumps({'status':'ok','email':'other@example.com','credits':100}), '')
        with self.assertRaises(AppError):adapter.credits()

    def test_published_cli_error_charged_no_preserved(self):
        original=self.cli.call
        def error(args,timeout=1800):
            if args[:2]==['video','i2v']:
                return 36,json.dumps({'status':'fail','error':{'detail':'Original backend refusal','charged':'no'}}),''
            return original(args,timeout)
        self.cli.call=error;batch=self.plan(1)
        self.e.begin('test',batch['id'],True,True);self.wait()
        row=self.e.state['batch']['rows'][0]
        self.assertEqual(row['charged'],'no')
        self.assertIn('Original backend refusal',row['error'])


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.e=Engine(root,FakeCLI(root),no_spend=True)
        self.server=Server(('127.0.0.1',0),self.e)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=self.server.origin

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()

    def post(self,path,body,headers=None):
        defaults={'Content-Type':'application/json','X-Flow-Token':self.server.token}
        defaults.update(headers or {})
        return urlopen(Request(self.url+path,json.dumps(body).encode(),defaults),timeout=5)

    def test_token_required_and_foreign_origin_rejected(self):
        for headers in [{'X-Flow-Token':'wrong'},{'Origin':'https://evil.example'},{'Host':'evil.example'}]:
            with self.assertRaises(HTTPError) as raised:self.post('/api/plan',{},headers)
            self.assertEqual(raised.exception.code,403)

    def test_server_blocks_spending_without_dry_run(self):
        with self.assertRaises(HTTPError) as raised:self.post('/api/test',{'confirmed':True,'risk':True})
        self.assertEqual(raised.exception.code,400)
        self.assertEqual(self.e.cli.calls,[])

    def test_upload_and_plan_via_http_then_no_spend_gate(self):
        with self.post('/api/stage',{'name':'test.png','content':base64.b64encode(PNG).decode()}) as response:image=json.load(response)
        with self.post('/api/plan',{'model':'omni-1.2','items':[{'id':image['id'],'prompt':'Exactly this prompt.  '}]}) as response:batch=json.load(response)
        self.assertEqual(batch['rows'][0]['prompt'],'Exactly this prompt.  ')
        with self.assertRaises(HTTPError) as raised:self.post('/api/test',{'batch_id':batch['id'],'confirmed':True,'risk':True})
        self.assertEqual(raised.exception.code,400)
        self.assertEqual(self.e.cli.calls,[])

    def test_repository_command_injection_not_executed(self):
        with self.assertRaises(HTTPError):self.post('/api/install',{'repository':'https://github.com/ffroliva/gflow-cli; calc.exe'})
        self.assertEqual(self.e.cli.calls,[])

    def test_mp4_range_download_and_media_authentication(self):
        image=self.e.stage('test.png',base64.b64encode(PNG).decode())
        batch=self.e.plan([{'id':image['id'],'prompt':'local'}],'omni-1.2')
        file=self.e.output/batch['id']/'test.mp4';file.write_bytes(MP4)
        url=self.url+'/media/'+batch['id']+'/test.mp4'
        with self.assertRaises(HTTPError) as raised:urlopen(url,timeout=5)
        self.assertEqual(raised.exception.code,403)
        request=Request(url,headers={'X-Flow-Token':self.server.token,'Range':'bytes=0-15'})
        with urlopen(request,timeout=5) as response:
            self.assertEqual(response.status,206)
            self.assertEqual(response.read(),MP4[:16])
            self.assertEqual(response.headers['Accept-Ranges'],'bytes')


if __name__=='__main__': unittest.main(verbosity=2)
