"""Deterministic metrics, model-visible inference, diagnostics inputs and portable releases."""
from __future__ import annotations
import base64
import json
import math
import mimetypes
import os
import random
import re
import shutil
import stat
import time
import urllib.request
import zipfile
from collections import Counter
from collections import defaultdict
from pathlib import Path
if __package__:
    from storage.artifacts import read
    from storage.artifacts import rows
    from storage.artifacts import write
    from storage.artifacts import jsonl
    from storage.artifacts import unique
else:
    from artifacts import read
    from artifacts import rows
    from artifacts import write
    from artifacts import jsonl
    from artifacts import unique


_INPUT_LIMIT = 512 * 1024 * 1024
_RECORD_LIMIT = 64 * 1024 * 1024
# Only these contract fields declare files. Provenance, prompts, commands and
# other strings must never cause the service to open a path.
_RESOURCE_FIELDS = frozenset({
    'media', 'image', 'image_path', 'image_paths', 'source_image', 'source_mask',
    'source_mesh', 'source_gaussian', 'raw_depth_path', 'depth_path', 'mask_path',
    'mesh_path',
})
_PROVENANCE_FIELDS = frozenset({'provenance', 'source_provenance', 'history'})


# 读取当前服务访问范围；独立导出的评分脚本不依赖服务运行时。
def _access():
    # The exported score.py intentionally runs with standard Python alone.
    if not __package__:
        return None
    from service_access.access_scope import current_access
    return current_access()


# 解析评测输入路径，在服务上下文中限制为可信产物或授权工作区文件。
def _input_path(value, *, base=None):
    path = Path(value)
    if base is not None and not path.is_absolute():
        path = base / path
    if _access() is not None:
        from storage.operation_outputs import trusted_output_path
        if trusted_output_path(path):
            return path
        from service_access.access_scope import workspace_path
        return workspace_path(path)
    return path.absolute()


# 在访问范围和大小限制内读取输入字节，保留可信服务产物的直接读取路径。
def _input_bytes(path, *, limit=_INPUT_LIMIT):
    path = _input_path(path)
    access = _access()
    if access is not None:
        from storage.operation_outputs import trusted_output_path
        if trusted_output_path(path):
            return path.read_bytes()
        from storage.confined_files import read_bytes
        return read_bytes(access.root_path, path, limit=limit)
    return path.read_bytes()


# 读取评测JSONL记录，有访问范围时使用受限文件读取。
def _input_rows(path):
    if _access() is None:
        return rows(path)
    text = _input_bytes(path, limit=_RECORD_LIMIT).decode('utf-8-sig')
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# 按授权范围复制评测输入，独立执行时拒绝符号链接源。
def _copy_input(source, destination):
    source = _input_path(source)
    access = _access()
    if access is not None:
        from storage.operation_outputs import trusted_output_path
        if trusted_output_path(source):
            shutil.copyfile(source, destination)
            return
        from storage.confined_files import copy_input
        copy_input(access.root_path, source, destination, limit=_INPUT_LIMIT)
    else:
        if source.is_symlink():
            raise PermissionError('Symbolic links are not release inputs')
        shutil.copyfile(source, destination)


# 只枚举普通文件条目，不沿链接目录遍历复现包。
def _bundle_files(root):
    """Enumerate only regular entries; never descend through a linked directory."""
    if root.is_symlink():
        raise PermissionError('Release directories cannot be symbolic links')
    for directory, folders, names in os.walk(root, followlinks=False):
        for name in folders:
            if (Path(directory) / name).is_symlink():
                raise PermissionError('Release directories cannot contain symbolic links')
        for name in names:
            path = Path(directory) / name
            if not stat.S_ISREG(path.lstat().st_mode):
                raise PermissionError('Release inputs must be regular files')
            yield path


# 持有已验证文件句柄写入压缩包，避免压缩时跟随符号链接。
def _zip_tree(root, destination):
    """Hold file descriptors while archiving; ZipFile.write follows symlinks."""
    root = Path(root).absolute()
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in _bundle_files(root):
            if os.name == 'posix':
                from storage.confined_files import open_read
                stream = open_read(root, path)
            else:
                stream = path.open('rb')
            with stream as source, archive.open(path.relative_to(root).as_posix(), 'w') as output:
                shutil.copyfileobj(source, output, 1024 * 1024)


# 复制复现目录和明确声明的外部资源，重写可移植引用并保留来源字段。
def _reproduce_bundle(bundle, copied):
    bundle = _input_path(bundle)
    if not bundle.is_dir():
        raise ValueError('Reproduction bundle must be a directory')
    files = list(_bundle_files(bundle))
    copied.mkdir()
    for source in files:
        destination = copied / source.relative_to(bundle)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _copy_input(source, destination)
    external = copied / 'inputs'
    external.mkdir(exist_ok=True)
    mapping = {}

    # 递归重定位声明的本地资源路径，保留远程引用及原始来源元数据。
    def relocate(value, field='', descriptor=False):
        if field in _PROVENANCE_FIELDS:
            return value
        if isinstance(value, dict):
            return {key: relocate(child, field if field == 'image_paths' else key,
                                  field == 'resources') for key, child in value.items()}
        if isinstance(value, list):
            return [relocate(child, field, descriptor) for child in value]
        declared = field in _RESOURCE_FIELDS or descriptor and field in ('path', 'file')
        if not isinstance(value, str) or not declared or not value:
            return value
        # Remote references stay remote; packaging does not fetch them.
        if value.startswith(('http://', 'https://', 'data:')):
            return value
        path = Path(value)
        prefixed = value.replace('\\', '/').startswith(bundle.name + '/')
        source = _input_path(path, base=bundle.parent if prefixed else bundle)
        try:
            relative = source.relative_to(bundle)
        except ValueError:
            relative = None
        if relative is not None and '..' not in relative.parts:
            return relative.as_posix()
        # Scope validation and descriptor-relative copy apply even when JSON
        # declares a file outside the reproduction directory.
        key = str(source)
        if key not in mapping:
            destination = external / f'input_{len(mapping):06d}{source.suffix}'
            # Never overwrite an existing bundle input with a relocated file.
            while destination.exists():
                destination = destination.with_name('_' + destination.name)
            _copy_input(source, destination)
            mapping[key] = destination.relative_to(copied).as_posix()
        return mapping[key]

    for source in files:
        path = copied / source.relative_to(bundle)
        if path.suffix == '.jsonl':
            jsonl(path, [relocate(row) for row in rows(path)])
        elif path.suffix == '.json':
            write(path, relocate(read(path)))
    write(copied / 'relocation.json', {
        'external_files_copied': len(mapping),
        'note': 'Only declared media/resource fields are relocated. Other strings are inert provenance.',
    })


# 按题目、通用记录或评测标识的优先级读取统一字符串身份。
def identity(row):
    return str(row.get('item_id',row.get('id',row.get('eval_id',''))))


# 去除首尾空白、合并连续空白并忽略大小写，规范化文本答案。
def normalized(value):
    return ' '.join(str(value).strip().casefold().split())


# 把列表、JSON列表或常见分隔文字转换为序列答案。
def sequence(value):
    if isinstance(value,list):return value
    if isinstance(value,str):
        try:
            parsed=json.loads(value)
            if isinstance(parsed,list):return parsed
        except ValueError:pass
        return [v.strip() for v in re.split(r'[,;>\s]+',value.strip('[] ')) if v.strip()]
    return []


# 按记录指定指标计算集合、顺序、数值或结构化答案分数，兼容单一答案封装。
def metric(item,pred):
    gold=item['answer']
    name=item.get('metric_id','exact_match')
    kind=item.get('answer_type','')
    # OpenAI-compatible respondents commonly wrap scalar/list answers. Recognize
    # only a single documented answer envelope; preserve structured JSON answers.
    if name!='json_field_accuracy' and isinstance(pred,dict) and set(pred)=={'answer'}:
        pred=pred['answer']
    if pred is None:return 0.0
    if name in ('set_f1','multi_choice_f1') or kind=='multi_choice':
        a={normalized(v) for v in sequence(gold)};b={normalized(v) for v in sequence(pred)}
        return 2*len(a&b)/(len(a)+len(b)) if a or b else 1.0
    if name=='pairwise_order_accuracy':
        a=sequence(gold);b=sequence(pred)
        if len(set(b))!=len(b) or set(a)!=set(b):return 0.0
        pairs=[(x,y) for i,x in enumerate(a) for y in a[i+1:]]
        return sum(b.index(x)<b.index(y) for x,y in pairs)/len(pairs) if pairs else float(a==b)
    if name in ('order_exact_accuracy','ordered_exact') or kind=='ordered_list':
        return float([normalized(v) for v in sequence(gold)]==[normalized(v) for v in sequence(pred)])
    if name in ('numeric_tolerance','pose_range','counting_accuracy') or kind=='number':
        try:
            a,b=float(gold),float(pred)
            if not math.isfinite(a) or not math.isfinite(b):return 0.0
            error=abs(a-b)
            tolerance=float(item.get('tolerance',0))
            if name=='pose_range':return 1.0 if error<=tolerance else max(0.0,1-(error-tolerance)/float(item.get('decay',20)))
            if name=='counting_accuracy':return 1.0 if error==0 else 0.5 if error==1 else 0.0
            return float(error<=max(tolerance,float(item.get('relative_tolerance',0))*abs(a)))
        except (ValueError,TypeError):return 0.0
    if name=='json_field_accuracy':
        if isinstance(pred,str):
            try:pred=json.loads(pred)
            except ValueError:return 0.0
        if not isinstance(gold,dict) or not isinstance(pred,dict):return 0.0
        return sum(pred.get(k)==v for k,v in gold.items())/len(gold) if gold else float(not pred)
    if name=='traffic_light_action':
        if normalized(pred)==normalized(gold):return 1.0
        return 0.5 if normalized(pred) in ('stop','proceed') and normalized(gold) in ('stop','proceed') else 0.0
    if name in ('accuracy','exact_match','normalized_exact','order_exact_accuracy'):
        if name=='exact_match':return float(json.dumps(gold,sort_keys=True)==json.dumps(pred,sort_keys=True))
        return float(normalized(gold)==normalized(pred))
    raise ValueError(f'Unsupported deterministic metric {name}; provide an explicit metric plugin rather than silently using exact match')


# 检查题目和预测唯一性及未知标识，计算逐题分数与各维汇总。
def score_predictions(items,predictions,model='saved_predictions'):
    expected=unique([{**r,'id':identity(r)} for r in items])
    mapped=unique([{**r,'id':identity(r)} for r in predictions])
    if not expected:raise ValueError('Cannot evaluate an empty dataset')
    extra=set(mapped)-set(expected)
    if extra:raise ValueError(f'Unknown prediction IDs: {sorted(extra)}')
    scored=[]
    for iid,item in expected.items():
        response=mapped.get(iid,{})
        prediction=response.get('prediction',response.get('answer'))
        effective=prediction.get('answer') if isinstance(prediction,dict) and set(prediction)=={'answer'} and item.get('metric_id')!='json_field_accuracy' else prediction
        present=iid in mapped and effective is not None and (not isinstance(effective,str) or bool(effective.strip()))
        scored.append({'id':iid,'item_id':iid,'model':model,'prediction':prediction,
            'score':metric(item,prediction) if present else 0.0,'present':present,
            'template_id':item.get('template_id',item.get('template','unspecified')),
            'source':item.get('source',item.get('source_name',item.get('scene','unspecified'))),
            'capability_tags':item.get('capability_tags',item.get('capabilities',[])),
            'difficulty':item.get('difficulty_level','unspecified')})
    # 汇总一组题目的平均分和缺失数量。
    def aggregate(values):return {'items':len(values),'score':sum(v['score'] for v in values)/len(values),
                                  'missing':sum(not v['present'] for v in values)}
    groups={}
    for key in ('template_id','source','difficulty','capability_tags'):
        grouped=defaultdict(list)
        for row in scored:
            vals=row[key] if isinstance(row[key],list) else [row[key]]
            for value in vals:grouped[str(value)].append(row)
        groups[key]={k:aggregate(v) for k,v in grouped.items()}
    return {'model':model,'overall':aggregate(scored),'groups':groups,'scores':scored,
            'error_taxonomy':{'missing':sum(not r['present'] for r in scored),
                'incorrect':sum(r['present'] and r['score']==0 for r in scored),
                'partial_credit':sum(0<r['score']<1 for r in scored)}}


# 读取题目与保存的预测，输出逐题评分文件及指标摘要。
def evaluate(args,directory,config):
    items=_input_rows(args['items'])
    result=score_predictions(items,_input_rows(args['predictions']),args.get('model','saved_predictions'))
    jsonl(directory/'score_items.jsonl',result.pop('scores'))
    write(directory/'metrics.json',result)
    return {**result,'scores':str(directory/'score_items.jsonl'),'metrics':str(directory/'metrics.json')}


# 从题目中仅提取模型可见题面、媒体、选项和答案类型。
def public_row(row,media):
    result={'id':identity(row),'question':row['question'],'media':media}
    if row.get('options',row.get('choices')) is not None:result['choices']=row.get('options',row.get('choices'))
    result['answer_type']=row.get('answer_type','short_text')
    return result


# 复制并分离公开题目媒体与私有标准答案，生成独立评分和复现包。
def package(args,directory,config):
    directory.mkdir(parents=True,exist_ok=True)
    root=directory/'benchmark'
    pub=root/'public';private=root/'authority'
    (pub/'media').mkdir(parents=True);private.mkdir()
    items_path=_input_path(args['items'])
    source_items=_input_rows(items_path);unique([{**r,'id':identity(r)} for r in source_items])
    public=[];gold=[]
    for i,row in enumerate(source_items):
        media=row.get('media',[row['image']] if row.get('image') else [])
        if isinstance(media,str):media=[media]
        relocated=[]
        for j,value in enumerate(media):
            source=_input_path(value,base=items_path.parent)
            if source.suffix.lower() not in ('.png','.jpg','.jpeg','.webp','.bmp','.gif','.mp4','.wav'):
                raise ValueError('Public media must not contain privileged arrays or label files')
            dest=pub/'media'/f'{i:06d}_{j}{source.suffix.lower()}'
            _copy_input(source,dest);relocated.append(dest.relative_to(pub).as_posix())
        public.append(public_row(row,relocated))
        gold.append({**row,'id':identity(row),'media':['../public/'+p for p in relocated]})
    jsonl(pub/'items.jsonl',public);jsonl(private/'items.jsonl',gold)
    shutil.copyfile(Path(__file__),root/'score.py')
    source = Path(__file__).resolve().parents[3]
    for module in (source/'storage/artifacts.py', source/'storage/json_io.py', source/'service_access/processes.py'):
        shutil.copyfile(module, root/module.name)
    if args.get('bundle'):
        _reproduce_bundle(args['bundle'],root/'reproduce')
    (root/'README.md').write_text('''# Portable benchmark

`public/` is the only model-visible input. `authority/` contains answers and audit data.

Score saved predictions (standard Python only):

    python -X utf8 score.py --items authority/items.jsonl --predictions predictions.jsonl --out metrics.json

Each prediction is `{"id":"...","answer":"..."}`. Missing predictions score zero; duplicate/unknown IDs are rejected.

When supplied, `reproduce/` contains the caller's reproduction bundle and copied inputs. Keep it private during model inference.
''',encoding='utf-8')
    _zip_tree(pub,directory/'benchmark-public.zip')
    _zip_tree(root,directory/'benchmark-complete.zip')
    return {'public_package':str(directory/'benchmark-public.zip'),'complete_package':str(directory/'benchmark-complete.zip'),
            'public':str(pub),'authority':str(private),'count':len(public)}


# 只将白名单公开字段发送给配置模型，记录预测与失败供后续评分。
def model_eval(args,directory,config):
    public=_input_rows(args['public_items'])
    # Construct allowlisted request rows even if caller accidentally passes audit items.
    configs=config.get('models',{})
    names=args.get('models') or [name for name, spec in configs.items()
                                 if spec.get('url') and spec.get('model') and 'python' not in spec
                                 and 'path' not in spec]
    if not names:raise ValueError('Configure named models or explicitly request baselines')
    items_path=_input_path(args['public_items'])
    results=[]
    for name in names:
        if name not in configs:raise ValueError(f'Model not configured: {name}')
        cfg=configs[name];predictions=[];failures=[]
        if not cfg.get('url') or not cfg.get('model'):
            raise ValueError(f'Model {name} must declare an HTTP chat URL and model ID for evaluation')
        for index,row in enumerate(public):
            content=[{'type':'text','text':json.dumps(public_row(row,[]),ensure_ascii=False)+'\nReturn only the option key for a choice question (e.g. A), a number for a numeric question, or the requested JSON array/object. Do not include reasoning.'}]
            media=row.get('media',[])
            if isinstance(media,str):media=[media]
            for value in media:
                path=_input_path(value,base=items_path.parent)
                mime=mimetypes.guess_type(path.name)[0] or 'image/png'
                content.append({'type':'image_url','image_url':{'url':'data:'+mime+';base64,'+base64.b64encode(_input_bytes(path)).decode()}})
            body={'model':cfg['model'],'messages':[{'role':'user','content':content}],
                  'temperature':cfg.get('temperature',0),'max_tokens':cfg.get('max_tokens',4096),'stream':False,**cfg.get('parameters',{})}
            headers={'Content-Type':'application/json'}
            token=cfg.get('token') or os.environ.get(cfg.get('token_env',''))
            if token:headers['Authorization']='Bearer '+token
            url=cfg['url'].rstrip('/')
            if not url.endswith('/chat/completions'):url+=('/chat/completions' if url.endswith('/v1') else '/v1/chat/completions')
            last=None
            for attempt in range(cfg.get('retries',1)+1):
                try:
                    request=urllib.request.Request(url,data=json.dumps(body).encode(),headers=headers)
                    opener=urllib.request.build_opener(urllib.request.ProxyHandler({})) if urllib.parse.urlsplit(url).hostname in ('localhost','127.0.0.1') else urllib.request.build_opener()
                    with opener.open(request,timeout=cfg.get('timeout_seconds',180)) as response:raw=json.load(response)
                    write(directory/f'model_{names.index(name)}/response_{index:06d}_attempt_{attempt}.json',{'id':identity(row),'model':name,'response':raw,'attempt':attempt,'images_sent':len(media)})
                    answer=raw['choices'][0]['message'].get('content') or ''
                    answer=answer.strip()
                    if not answer:raise ValueError('Model returned an empty answer; check reasoning/output token budget. Raw response retained.')
                    try:answer=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',answer))
                    except ValueError:pass
                    predictions.append({'id':identity(row),'answer':answer,'model':name})
                    write(directory/f'model_{names.index(name)}/response_{index:06d}.json',{'id':identity(row),'model':name,'response':raw,'attempt':attempt,'images_sent':len(media)})
                    last=None;break
                except (OSError,ValueError,KeyError) as exc:
                    last=str(exc)
                    if attempt<cfg.get('retries',1):time.sleep(1)
            if last:failures.append({'id':identity(row),'error':last})
        out=directory/f'model_{names.index(name)}'
        jsonl(out/'predictions.jsonl',predictions);jsonl(out/'failures.jsonl',failures)
        result={'model':name,'predictions':str(out/'predictions.jsonl'),'failed':len(failures),'completed':len(predictions),'evaluation_mode':'real_model'}
        if args.get('items'):
            score=score_predictions(_input_rows(args['items']),predictions,name)
            jsonl(out/'score_items.jsonl',score.pop('scores'));write(out/'metrics.json',score)
            result.update(scores=str(out/'score_items.jsonl'),overall=score['overall'])
        results.append(result)
    write(directory/'models.json',results)
    return {'models':results,'status':'completed' if all(not r['failed'] for r in results) else 'incomplete'}


# 仅使用公开选项运行首选项和随机选项基线，保存预测及分数。
def baselines(args,directory,config):
    items=_input_rows(args['items']);rng=random.Random(args.get('seed',42))
    results=[]
    for name in ('first_choice','random_choice'):
        preds=[]
        for row in items:
            choices=row.get('options',row.get('choices',{}))
            values=list(choices) if choices else []
            # Responders see only choices, never the gold answer.
            pred=(values[0] if name=='first_choice' else rng.choice(values)) if values else None
            preds.append({'id':identity(row),'answer':pred})
        score=score_predictions(items,preds,name)
        out=directory/name
        jsonl(out/'predictions.jsonl',preds);jsonl(out/'score_items.jsonl',score.pop('scores'))
        write(out/'metrics.json',score)
        results.append({'model':name,'scores':str(out/'score_items.jsonl'),'overall':score['overall']})
    return {'models':results,'evaluation_mode':'proxy','note':'Deterministic/random controls are not independent model respondents or empirical evidence of IRT validity.'}


# 合并唯一的模型题目评分，生成总体及分层指标报告。
def report(args,directory,config):
    paths=args['scores'] if isinstance(args['scores'],list) else [args['scores']]
    matrix=[r for path in paths for r in _input_rows(path)]
    seen=set()
    for r in matrix:
        key=(r['model'],identity(r))
        if key in seen:raise ValueError(f'Duplicate model/item response {key}')
        seen.add(key)
    jsonl(directory/'score_matrix.jsonl',matrix)
    grouped=defaultdict(list)
    for row in matrix:grouped[row['model']].append(row)
    summary={k:{'items':len(v),'mean_score':sum(float(r['score']) for r in v)/len(v),
                'missing':sum(not r.get('present',True) for r in v)} for k,v in grouped.items()}
    strata={}
    for model,model_rows in grouped.items():
        strata[model]={}
        for dimension in ('template_id','source','difficulty','capability_tags'):
            groups=defaultdict(list)
            for row in model_rows:
                values=row.get(dimension,'unspecified')
                for value in values if isinstance(values,list) else [values]:groups[str(value)].append(row)
            strata[model][dimension]={key:{'items':len(value),'mean_score':sum(r['score'] for r in value)/len(value)} for key,value in groups.items()}
    write(directory/'stratified_metrics.json',strata)
    write(directory/'metrics.json',summary)
    text='# Evaluation report\n\n| Model | Items | Mean score | Missing |\n|---|---:|---:|---:|\n'
    text+=''.join(f"| {k} | {v['items']} | {v['mean_score']:.4f} | {v['missing']} |\n" for k,v in summary.items())
    text+='\nScores describe this collection only. Proxy responders and small samples do not establish model ranking or diagnostic validity.\n'
    (directory/'report.md').write_text(text,encoding='utf-8')
    return {'report':str(directory/'report.md'),'matrix':str(directory/'score_matrix.jsonl'),'strata':str(directory/'stratified_metrics.json'),'models':summary}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--items',required=True);parser.add_argument('--predictions',required=True);parser.add_argument('--out',required=True)
    args=parser.parse_args()
    result=score_predictions(rows(args.items),rows(args.predictions));write(args.out,result)
    print(json.dumps(result['overall']))
