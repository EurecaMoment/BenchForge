"""Generate a new BenchForge mode. Never writes official presets or profiles."""
import argparse
from copy import deepcopy
from pathlib import Path
import yaml


class JavaScript(str):
    pass


class Loader(yaml.SafeLoader):
    pass


class Dumper(yaml.SafeDumper):
    pass


Loader.add_constructor('tag:yaml.org,2002:js', lambda loader,node:JavaScript(loader.construct_scalar(node)))
Dumper.add_representer(JavaScript, lambda dumper,value:dumper.represent_scalar('tag:yaml.org,2002:js',str(value)))

PERSONA = '''You operate BenchForge, an independent benchmark construction mode.
Keep Standard's conversational interaction, file tools, image inspection and task code.
Use direct tools, PTC or subagents according to the task; parallelize independent work when useful.
Call benchforge_catalog to discover sources, annotation, simulation, evidence, packaging and scoring.
This is a partial migration. Consult catalog section=migration for original skill gaps. A reference template or your ability to write task code does not mean its original capability is bundled or validated.
Choose the next useful operation from actual artifacts. There is no mandatory five-stage protocol, tmux scheduler, DONE marker or fixed model roster.
Write and revise task code for acquisition, cleaning, template generation and custom metrics in the task workspace. The old stage knowledge is a reference, not an execution scheduler.
Inspect actual images with benchforge_view_image or Standard's image tools; use the results to improve the task.
Use source JSON selectors or reproducible program outputs for answers. Retain original labels and simulator state. Model masks and inferred depth remain predictions. Never let a reviewer replace GT.
For simulation distance questions, read depth_semantics and camera intrinsics: camera-forward Z and Euclidean range differ. Match the oracle to the wording and the visible marker footprint. Keep raw arrays and oracle code in private assets, not public media. Source JSON paths and media/assets paths resolve relative to the input evidence JSONL.
Build separates public questions/media from authority data. Evaluate saved predictions when requested; model API runs require task-specific configuration.
Change task code and inputs, not harness implementation or service configuration. Report actual artifacts, counts and remaining gaps. A capture process exit is not quality acceptance.
Keep verification targeted to the requested deliverable. Do not generate checksum manifests or perform broad filesystem audits unless the user explicitly requests them.
'''


def build(sources, root, python, config_path):
    def preset(name):
        return next(row for patch in sources[name] for row in patch.get('insert',[]) if row['id']=='preset-'+name)
    result=deepcopy(preset('standard'))
    result['id']='preset-benchforge'
    cfg=result['config']
    cfg.update(id='benchforge',name='BenchForge',order=-2,
               description='Composable benchmark construction, annotation, simulation and evidence.')
    plugins=cfg['plugins']
    persona=next(p for p in plugins if p['id']=='persona')
    persona['config']['prefix']=PERSONA
    presentation=deepcopy(next(p for p in preset('ptc')['config']['plugins'] if p['id']=='tool-presentation'))
    presentation['config']['mode']='both'
    plugins.append(presentation)
    persistent=deepcopy(next(p for p in preset('minimal')['config']['plugins'] if p['id']=='persistent-shell'))
    for row in persistent['config']:
        if row['id'] in ('persistent-bash','persistent-pwsh'):
            shell='bash' if row['id']=='persistent-bash' else 'pwsh'
            row['name']=str(Path(__file__).with_name('persistent-shell.mjs').resolve())
            row['config']={'shell':shell,'timeoutMs':row['config']['timeoutMs'],'dshRoot':str(root.resolve())}
    plugins.append(persistent)
    plugins.append(deepcopy(next(p for p in preset('cordis')['config']['plugins'] if p['id']=='tool-cordis')))
    plugins.append({'id':'benchforge-tools','name':str(Path(__file__).with_name('tools.mjs').resolve()),
                    'config':{'dshRoot':str(root.resolve()),'python':python,'configPath':str(config_path.resolve())}})
    return [{'insert':[result]}]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dsh-root',required=True,type=Path)
    parser.add_argument('--python',default='python')
    parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    base=args.dsh_root/'packages/bundle/web-app/presets'
    sources={name:yaml.load((base/(name+'.patch.yml')).read_text(encoding='utf-8'),Loader=Loader) for name in ['standard','ptc','minimal','cordis']}
    result=build(sources,args.dsh_root,args.python,args.config)
    args.output.write_text(yaml.dump(result,Dumper=Dumper,sort_keys=False,allow_unicode=True),encoding='utf-8')


if __name__=='__main__':
    main()
