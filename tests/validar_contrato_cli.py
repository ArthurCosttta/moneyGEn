"""Real published CLI parsing and DTOs; the generation transport is mocked."""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
from click.testing import CliRunner
from gflow_cli.cli import main
import gflow_cli.cli_video as video
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root));sys.path.insert(0,str(root/'tests'))
from engine import Engine
from test_flowlocal import FakeCLI,PNG

scenarios=[
 {'model':'omni-1.2','aspect':'9:16','duration':6,'resolution':None},
 {'model':'omni-1.2','aspect':'16:9','duration':10,'resolution':'720p'},
 {'model':'veo-lite','aspect':'9:16','duration':None,'resolution':None},
]
results=[]
for scenario in scenarios:
 with tempfile.TemporaryDirectory(dir=root) as tmp:
  app=Path(tmp);cli=FakeCLI(app);engine=Engine(app,cli)
  image=engine.stage('image.png',base64.b64encode(PNG).decode())
  prompt='  --literal "ação"\n$HOME & fim.  '
  batch=engine.plan([{'id':image['id'],'prompt':prompt}],**scenario)
  engine.begin('test',batch['id'],True,True);engine.worker.join(10)
  args=next(c for c in cli.calls if c[0]=='video')
  captured={}
  async def record(request,*args,**kwargs):captured['request']=request
  env={'GFLOW_CLI_HOME':str(app/'flow'),'GFLOW_CLI_TELEMETRY':'0','GFLOW_CLI_UPDATE_CHECK':'0'}
  with patch.dict(os.environ,env),patch.object(video,'_make_provider_dir',return_value=app),patch.object(video,'_generate_and_report',side_effect=record):
   result=CliRunner().invoke(main,args)
  if result.exit_code:raise AssertionError((result.exit_code,result.output,repr(result.exception)))
  request=captured['request']
  assert request.prompt==prompt
  assert request.duration==scenario['duration'] and request.count==1
  assert request.aspect.value==('portrait' if scenario['aspect']=='9:16' else 'landscape')
  assert request.resolution==scenario['resolution']
  assert request.start_image.is_file()
  results.append({**scenario,'parsed_model':request.model.value,'exact_prompt':True})
print(json.dumps({'published_cli':'0.103.1','scenarios':results,'no_flow_calls':True},ensure_ascii=False))
