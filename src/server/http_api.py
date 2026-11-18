import argparse
import faulthandler
import signal
import hmac
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import html
import json
import logging
from pathlib import Path
import threading
from functools import partial
from urllib.parse import quote
from urllib.parse import urlparse
from urllib.parse import unquote
from storage.files import safe_path
from pipelines.scene_production.service import create_store
from pipelines.scene_production.engine import coordinate
from capabilities.asset_generation.generation import GenerationTools
from capabilities.scene_authoring.program_submission import read_program_submission
from pipelines.scene_production.preflight import read_capture_submission, inspect_capture_submission
from capabilities.asset_generation.layout import start_design_request
from capabilities.asset_generation.layout import require_prior_reference
from server import scene_requests as _scene_requests
from server.scene_requests import context_from_request as _request_context
from storage.scene_jobs.workbench_queries import WORKBENCH_QUERIES
from storage.scene_jobs.workbench_queries import read_connection


scene_intent = _scene_requests.scene_intent


def request_error_status(method, exc):
    if isinstance(exc, FileNotFoundError) or (isinstance(exc, KeyError) and method == 'GET'):
        return 404
    if isinstance(exc, PermissionError):
        return 403
    if isinstance(exc, (KeyError, TypeError, ValueError)):
        return 400
    return 500


def gallery_path(run_id):
    return '/gallery/' + quote(str(run_id), safe='')


def request_error_payload(exc, status):
    """Preserve complete domain diagnostics beside the bounded error summary."""
    from capabilities.scene_authoring.validation.diagnostics import ScenePreflightError
    from capabilities.scene_authoring.validation.contract_diagnostics import ProgramValidationError
    payload = {'error': type(exc).__name__,
               'detail': str(exc)[:1000] if status < 500 else 'Internal server error'}
    if isinstance(exc, ScenePreflightError):
        payload.update(code=exc.code, preflight=exc.report)
    elif isinstance(exc, ProgramValidationError):
        payload.update(code=exc.code, issues=exc.issues)
    return payload


def start_scene_request(store,value,max_scenes=1000,*,settings=None):
    return _scene_requests.start_scene_request(
        store,
        value,
        max_scenes,
        submission_reader=partial(read_capture_submission, settings=settings),
        prior_reference_checker=require_prior_reference,
    )


def start_generation_request(store,tool,value):
    from capabilities.asset_generation.generation_stage import validate_generation_request
    parameters=validate_generation_request(GenerationTools(store.root),tool,{k:v for k,v in value.items() if k not in {'request_key','context'}})
    intent={'tool':tool,'parameters':parameters}
    kwargs={}
    context=_request_context(value)
    if context is not None:kwargs['context']=context
    rid=store.submit(value['request_key'],[intent],'generation',**kwargs)
    return {'run_id':rid,'task_id':rid+'.scene0','state':'accepted','tool':tool}


def start_capture_request(store,value,*,settings=None):
    return _scene_requests.start_capture_request(
        store,
        value,
        submission_reader=partial(read_capture_submission, settings=settings),
    )


def refine_scene_request(store,value,*,settings=None):
    return _scene_requests.refine_scene_request(
        store,
        value,
        submission_reader=partial(read_capture_submission, settings=settings),
        prior_reference_checker=require_prior_reference,
    )


def extend_scene_request(store,value,*,settings=None):
    return _scene_requests.extend_scene_request(
        store,
        value,
        submission_reader=partial(read_capture_submission, settings=settings),
        prior_reference_checker=require_prior_reference,
    )


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args()
    faulthandler.enable()
    if hasattr(signal,'SIGUSR1'):faulthandler.register(signal.SIGUSR1)
    from service_access.runtime_config import load_service_config, apply_runtime_environment
    config=load_service_config(args.config)
    from service_access.configuration import isaac_config
    capture_settings = isaac_config(config)
    apply_runtime_environment(config)
    store=create_store(config['artifacts'],config.get('harness'),config.get('database_url'))
    from storage.scene_jobs.migrate_capture import migrate_capture
    logging.getLogger(__name__).info('Capture protocol migration: %s', migrate_capture(store))
    stop=threading.Event()
    thread=threading.Thread(target=coordinate,args=(store,stop),kwargs={'config':config},daemon=True)
    thread.start()

    class Handler(BaseHTTPRequestHandler):
        def fail(self, exc):
            status = request_error_status(self.command, exc)
            if status == 500:
                logging.getLogger(__name__).exception(
                    'request failed method=%s path=%s run_id=%s task_id=%s',
                    self.command, urlparse(self.path).path,
                    getattr(self, 'request_run_id', None),
                    getattr(self, 'request_task_id', None),
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
            self.reply(request_error_payload(exc, status), status)

        def reply(self,value,status=200):
            if status < 300 and isinstance(value, dict) and value.get('run_id'):
                from service_access.access_scope import current_access
                access = current_access()
                if access is not None:
                    from storage.access_identity import bind_run_scope
                    bind_run_scope(store.repo.engine, access.scope, value['run_id'])
            data=json.dumps(value,ensure_ascii=False,default=str).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def binary(self,data,mime='application/zip'):
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def do_GET(self):
            # Gallery and image routes share the operator authentication boundary.
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + config['operator_token']):
                self.reply({'error': 'unauthorized'}, 401)
                return
            try:
                path=unquote(urlparse(self.path).path)
                if path.startswith('/image/'):
                    file=safe_path(Path(config['artifacts']),path[len('/image/'):],must_exist=True)
                    if file.suffix.lower()!='.png':raise ValueError('only public PNG imagery')
                    data=file.read_bytes();mime='image/png'
                elif path.startswith('/gallery/'):
                    rid=path[len('/gallery/'):];self.request_run_id = rid;snap=store.snapshot(rid)
                    sections=[]
                    for task in snap['tasks']:
                        folder=store.directory(task['id'],task['unit']['revision'])
                        pics=sorted(folder.glob('capture/view_*.png'))
                        images=''.join('<a href="/image/'+p.relative_to(Path(config['artifacts'])).as_posix()+'"><img src="/image/'+p.relative_to(Path(config['artifacts'])).as_posix()+'"></a>' for p in pics)
                        result=task.get('result') or task.get('failure') or {'phase':task['unit']['phase']}
                        sections.append('<section><h2>'+html.escape(task['unit']['intent'].get('name',task['id']))+'</h2><p>'+html.escape(task['state'])+' · revision '+str(task['unit']['revision'])+'</p><div class="views">'+images+'</div><pre>'+html.escape(json.dumps(result,ensure_ascii=False,indent=2))+'</pre></section>')
                    body='<html><meta charset="utf-8"><meta http-equiv="refresh" content="30"><title>BenchForge</title><style>body{background:#111827;color:#e5e7eb;font:16px system-ui;margin:32px}h1{color:#67e8f9}.views{display:flex;gap:12px;flex-wrap:wrap}.views img{width:400px;border-radius:8px}section{padding:20px;background:#1f2937;margin:16px 0;border-radius:12px}pre{white-space:pre-wrap;font-size:13px}</style><h1>BenchForge · 真实生产产物</h1><p>'+html.escape(rid)+' · '+snap['state']+'</p>'+''.join(sections)+'</html>'
                    data=body.encode();mime='text/html; charset=utf-8'
                else:self.reply({'name':'BenchForge','message':'Use /gallery/<run_id>'},404);return
                self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            except Exception as exc:self.fail(exc)
        def do_POST(self):
            if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+config['operator_token']):self.reply({'error':'unauthorized'},401);return
            from service_access.access_scope import access_context, resolve_ticket
            try:
                ticket = self.headers.get('X-BenchForge-Scope')
                access = resolve_ticket(store.repo.engine, ticket, config['operator_token']) if ticket else None
                with access_context(access):
                    self.dispatch_post()
            except Exception as exc:
                self.fail(exc)

        def dispatch_post(self):
            if self.path == '/dataset':
                self.reply({
                    'error':'Legacy dataset jobs are retired. Use data.capability or POST /start with target_items.',
                },410)
                return
            try:
                length=int(self.headers.get('Content-Length','0'))
                limit=2*1024*1024
                if self.path in WORKBENCH_QUERIES or self.path == '/stage-review/read':
                    limit = 4096
                if self.path == '/session-search/replace':
                    limit = 32*1024*1024
                if length<0 or length>limit:raise ValueError('request size exceeds endpoint limit')
                raw=self.rfile.read(length)
                if len(raw)!=length:raise ValueError('incomplete request body')
                value=json.loads(raw or b'{}')
                from server.access_requests import authorize_request
                value = authorize_request(store, self.path, value)
                self.request_run_id = value.get('run_id') if isinstance(value, dict) else None
                self.request_task_id = value.get('task_id') if isinstance(value, dict) else None
                from capabilities.scene_authoring.validation.request_admission import check_request
                check_request(store.root,self.path,value)
                if self.path in ('/session-search/replace', '/session-search/query'):
                    from storage.session_search import session_search
                    self.reply(session_search(store.repo.engine, self.path.rsplit('/', 1)[1], value))
                elif self.path == '/records':
                    from service_access.access_scope import current_access
                    from service_access.runtime_config import environment_value
                    from database.engine import engine_for
                    from storage.access_queries import query_records
                    access = current_access()
                    if access is None:
                        raise PermissionError('Database reads require a verified conversation')
                    reader_url = environment_value(config.get('reader_database_url_env', 'BENCHFORGE_READER_DATABASE_URL'))
                    if not reader_url:
                        raise RuntimeError('Configure the dedicated PostgreSQL reader role')
                    after = value.get('after')
                    if after is not None and value['resource'] in ('events', 'operations', 'reviews'):
                        if not isinstance(after, str) or not after.isdecimal():
                            raise ValueError('Invalid query cursor')
                        after = int(after)
                    records = query_records(engine_for(reader_url), access.scope, value['resource'],
                                            limit=value.get('limit', 30), after=after)
                    if records['next'] is not None:
                        records['next'] = str(records['next'])
                    self.reply(records)
                elif self.path == '/stage-review/read':
                    from server.stage_review_queries import read_review_request
                    self.reply(read_review_request(config, value))
                elif self.path in WORKBENCH_QUERIES:
                    with read_connection(store.repo.engine) as connection:
                        result = WORKBENCH_QUERIES[self.path](connection, value)
                    self.reply(result)
                elif self.path=='/catalog':
                    from capabilities.scene_authoring.catalog.operators import catalog_response
                    self.reply(catalog_response(store,value,settings=capture_settings))
                elif self.path=='/layout':
                    self.reply(start_design_request(store,value))
                elif self.path in {'/diffusion','/sam3','/trellis','/depth','/mesh-import'}:
                    tool=self.path[1:].replace('-', '_')
                    self.reply(start_generation_request(store,tool,value))
                elif self.path=='/start':
                    self.reply(start_scene_request(store,value,config.get('max_scenes_per_run',1000),
                                                   settings=capture_settings))
                elif self.path=='/capture':
                    self.reply(start_capture_request(store,value,settings=capture_settings))
                elif self.path=='/preview':
                    from capabilities.scene_authoring.geometry.preview import preview_scene_geometry
                    from capabilities.isaac_sim.scene_requirements import validate_native_references
                    program=read_program_submission(store.root,value['scene_program_path'],policy={})['program']
                    validate_native_references(program,capture_settings)
                    def mesh(asset_id):
                        return json.loads(GenerationTools(store.root).asset_path(asset_id,'mesh.json').read_text())
                    self.reply(preview_scene_geometry(program,mesh_loader=mesh))
                elif self.path == '/scene-preflight':
                    self.reply(inspect_capture_submission(
                        store.root, value['scene_program_path'], execution_mode=value.get('execution_mode'),
                        settings=capture_settings))
                elif self.path=='/asset':
                    intent=value['intent']
                    if not isinstance(intent,dict) or not {'name','description'}<=set(intent) or set(intent)-{'name','description','label','source_image','source_mask','source_mesh','source_gaussian','asset_id','seed','source_up_axis','size_hint_m','subject_box','edit_reference'}:raise ValueError('invalid asset intent')
                    if not isinstance(intent['description'],str) or not 1<=len(intent['description'])<=12000:raise ValueError('invalid asset description')
                    context=_request_context(value)
                    kwargs={'context':context} if context is not None else {}
                    self.reply({'run_id':store.submit(value['request_key'],[intent],'asset',**kwargs),'state':'accepted'})
                elif self.path=='/extend':
                    self.reply(extend_scene_request(store,value,settings=capture_settings))
                elif self.path=='/refine':
                    self.reply(refine_scene_request(store,value,settings=capture_settings))
                elif self.path=='/observe':
                    from capabilities.capture.observation import observe
                    result=observe(store,value['run_id']);result.pop('cursor')
                    self.reply(result)
                elif self.path=='/wait':
                    from capabilities.capture.observation import wait_for_change
                    result=wait_for_change(store,value['run_id'],None,value.get('timeout_seconds',60),stop)
                    result.pop('cursor');self.reply(result)
                elif self.path=='/task-code':
                    from server.task_workspace import run_task_code
                    self.reply(run_task_code(store.root,value['workspace_id'],value['code'],value.get('timeout_seconds',60)))
                elif self.path=='/evidence':
                    from capabilities.capture.operator_evidence import evidence
                    self.reply(evidence(store,value['task_id'],value.get('revision'),value.get('file'),value.get('workspace_id')))
                elif self.path=='/production/acceptance':
                    from capabilities.scene_library.acceptance import accepted_scenes
                    self.reply(accepted_scenes(store, value.get('scene_sources')))
                elif self.path=='/retry':self.reply(store.retry(value['task_id'],value.get('feedback'),value.get('capture_mode','auto')))
                elif self.path=='/download':
                    task_id=value['task_id'];snap=store.snapshot(task_id.split('.')[0]);task=next(t for t in snap['tasks'] if t['id']==task_id)
                    directory=store.root/task_id/('revision_'+str(value.get('revision',task['unit']['revision'])));file=safe_path(directory,value['file'],must_exist=True)
                    if file.suffix not in {'.zip','.json','.jsonl','.usda','.usdc','.usdz','.glb','.png','.jpg','.jpeg','.npy','.npz','.ply','.obj','.gltf','.bin','.mp4'}:raise ValueError('unsupported artifact download')
                    self.binary(file.read_bytes(),'application/octet-stream')
                elif self.path=='/inspect':
                    state=store.snapshot(value['run_id'])
                    self.reply({'run_id':value['run_id'],'state':state['state'],'gallery_url':gallery_path(value['run_id']),'tasks':[{**{k:t.get(k) for k in ('id','state','unit','result','failure')},'downloads':[p.relative_to(store.directory(t['id'],t['unit']['revision'])).as_posix() for p in store.directory(t['id'],t['unit']['revision']).glob('release/*.zip')]} for t in state['tasks']]})
                elif self.path=='/cancel':store.cancel(value['run_id']);self.reply({'cancel_requested':True})
                else:self.reply({'error':'unknown operation'},404)
            except Exception as exc:self.fail(exc)
    server=ThreadingHTTPServer(('127.0.0.1',config.get('port',3841)),Handler)
    try:server.serve_forever()
    finally:stop.set();server.server_close()


if __name__=='__main__':main()
