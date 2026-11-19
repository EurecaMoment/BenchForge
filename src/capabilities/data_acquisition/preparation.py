"""Explicit source import and cleaning; original labels are immutable authority."""
from __future__ import annotations
import csv
import io
import json
import shutil
import urllib.request
from urllib.parse import urlsplit
from collections import Counter
from pathlib import Path
from capabilities.data_acquisition.source_files import (
    open_source, read_source as read, source_path, source_rows as rows, text_source,
)
from storage.artifacts import write
from storage.artifacts import jsonl
from storage.artifacts import unique
from storage.artifacts import run


# 按点分隔路径读取嵌套字典，路径缺失时返回默认值。
def field(row,path,default=None):
    value=row
    for key in path.split('.'):
        if not isinstance(value,dict) or key not in value:return default
        value=value[key]
    return value


# 将图片目录、JSON或JSONL按字段映射规范化为统一输入记录。
def normalize(args,directory,config):
    source=source_path(args['input'])
    kind=args.get('format','jsonl' if source.suffix=='.jsonl' else source.suffix.lstrip('.'))
    mapping=args.get('fields',{})
    data=[]
    if kind=='images':
        if not source.is_dir():raise ValueError('Image source must be a directory')
        pattern=args.get('pattern','*')
        if Path(pattern).is_absolute() or '..' in Path(pattern).parts:
            raise ValueError('Image pattern must stay inside its source directory')
        data=[{'id':str(i),'image':str(p)} for i,p in enumerate(sorted(source.glob(pattern))) if p.suffix.lower() in ('.png','.jpg','.jpeg','.webp')]
    elif kind=='jsonl':data=rows(source)
    elif kind=='json':
        value=read(source);data=value if isinstance(value,list) else field(value,args.get('records_key','items'),[])
    elif kind=='csv':
        with text_source(source) as stream:data=list(csv.DictReader(stream))
    elif kind=='parquet':
        try:import pyarrow.parquet as pq
        except ImportError:raise ValueError('Install benchforge[datasets] for Parquet / ERQA export support')
        with open_source(source) as stream:data=pq.read_table(stream).to_pylist()
    else:raise ValueError('Supported formats: jsonl, json, csv, parquet, images')
    if args.get('limit'):data=data[:args['limit']]
    provenance=args.get('provenance','official' if kind!='images' else 'unlabeled')
    if provenance not in ('official','human','simulation','program','unlabeled','prediction'):raise ValueError('Unknown source kind')
    root=source if source.is_dir() else source.parent
    if args.get('media_root'):root=source_path(args['media_root'])
    media_dir=directory/'media';media_dir.mkdir()
    normalized=[];rejected=[]
    for index,raw in enumerate(data):
        iid=str(field(raw,mapping.get('id','id'),field(raw,'question_id',f'item_{index:06d}')))
        values=field(raw,mapping.get('media','media'),field(raw,'images',field(raw,'image',[])))
        if not isinstance(values,list):values=[values] if values else []
        paths=[]
        try:
            for j,value in enumerate(values):
                dest=media_dir/f'{index:06d}_{j}.png'
                if isinstance(value,dict) and value.get('bytes'):
                    from PIL import Image
                    Image.open(io.BytesIO(value['bytes'])).convert('RGB').save(dest)
                else:
                    path=Path(value.get('path') if isinstance(value,dict) else value)
                    if not path.is_absolute():path=root/path
                    dest=dest.with_suffix(path.suffix.lower())
                    with open_source(path) as stream, dest.open('xb') as output:
                        shutil.copyfileobj(stream,output)
                paths.append(str(dest))
            # Preserve an unmodified JSON form of the original record; bytes are materialized separately.
            serial=json.loads(json.dumps(raw,default=lambda v:{'binary_materialized':True,'bytes':len(v)} if isinstance(v,bytes) else str(v)))
            original=directory/'original'/f'{index:06d}.json';write(original,serial)
            record={**serial,'id':iid,'record_id':iid,'sample_id':iid,'media':paths,
                'image_path':paths[0] if paths else None,'source_fields':serial,
                'provenance':{'kind':provenance,'path':str(original),'source_id':args.get('source_id',source.stem)},
                'source_type':provenance,'source_name':args.get('source_id',source.stem),
                'split':field(raw,mapping.get('split','split'),args.get('split','test'))}
            for name in ('objects','question','answer','capability_tags','depth_semantics'):
                value=field(raw,mapping.get(name,name))
                if value is not None:record[name]=value
            if provenance=='unlabeled':record['annotation_required']=True
            normalized.append(record)
        except (OSError,ValueError,TypeError) as exc:rejected.append({'id':iid,'error':str(exc)})
    unique(normalized)
    if not normalized:raise ValueError('No usable source records; inspect source format and media field mapping')
    jsonl(directory/'normalized.jsonl',normalized);jsonl(directory/'rejected.jsonl',rejected)
    inventory={'source_id':args.get('source_id',source.stem),'source_path':str(source),'format':kind,
        'records':len(normalized),'rejected':len(rejected),'media':sum(len(r['media']) for r in normalized),
        'fields':sorted({k for row in data for k in row}),
        'with_official_answers':sum('answer' in r for r in normalized) if provenance=='official' else 0,
        'annotation_required':sum(r.get('annotation_required',False) for r in normalized),'license':args.get('license','not supplied')}
    write(directory/'source_inventory.json',inventory)
    return {'input':str(directory/'normalized.jsonl'),'inventory':inventory,'rejected':str(directory/'rejected.jsonl')}


# 验证下载地址及单文件名，保存来源回执并规范化下载或本地输入。
def acquire(args,directory,config):
    if args.get('url'):
        if urlsplit(args['url']).scheme not in ('http','https'):
            raise ValueError('Source downloads require an HTTP or HTTPS URL')
        name=args.get('filename','source.jsonl')
        if (not isinstance(name,str) or not name or name in ('.','..')
                or '/' in name or '\\' in name or ':' in name):
            raise ValueError('Download filename must be a single portable file name')
        dest=directory/'download'/name;dest.parent.mkdir()
        request=urllib.request.Request(args['url'],headers={'User-Agent':'BenchForge/0.2'})
        with urllib.request.urlopen(request,timeout=args.get('timeout_seconds',120)) as response, dest.open('xb') as out:
            shutil.copyfileobj(response,out)
            write(directory/'download.json',{'url':args['url'],'status':response.status,'path':str(dest),'size':dest.stat().st_size})
        return normalize({**args,'input':str(dest)},directory,config)
    return normalize(args,directory,config)


# 校验样本标识和媒体可读性，分离有效与拒绝记录并保存清理报告。
def clean(args,directory,config):
    source=source_path(args['input']);data=rows(source)
    backend=args.get('backend','native')
    rejected=[];valid=[];seen=set()
    for row in data:
        iid=str(row.get('id',row.get('sample_id','')))
        reasons=[]
        if not iid or iid in seen:reasons.append('missing_or_duplicate_id')
        seen.add(iid)
        for media in row.get('media',[]):
            try:
                from PIL import Image
                with open_source(media,base=source.parent) as stream:
                    with Image.open(stream) as im:
                        im.verify()
                    stream.seek(0)
                    with Image.open(stream) as im:
                        if min(im.size)<args.get('min_side',32):reasons.append('image_too_small')
                        from PIL import ImageStat
                        gray=im.convert('L');stats=ImageStat.Stat(gray)
                        if stats.stddev[0]<args.get('min_contrast',1):reasons.append('near_constant_image')
                        if args.get('min_bright_fraction') is not None:
                            histogram=gray.histogram()
                            fraction=sum(histogram[17:])/sum(histogram)
                            if fraction<args['min_bright_fraction']:reasons.append('insufficient_visible_brightness')
            except (OSError,ValueError):reasons.append('unreadable_media')
        if not row.get('media') and args.get('require_media',True):reasons.append('missing_media')
        if reasons:rejected.append({'id':iid,'reasons':reasons,'record':row})
        else:valid.append(row)
    if backend=='data_juicer':
        cfg=config.get('cleaning',{})
        command=cfg.get('command')
        if not command:raise ValueError('Configure cleaning.command, e.g. ["dj-process"]. Install the optional Data-Juicer environment.')
        # Only derived text goes through cleaning. Official labels/state never get rewritten.
        input_path=directory/'text_input.jsonl';output_path=directory/'text_output.jsonl'
        text_field=args.get('text_field','question')
        jsonl(input_path,[{'id':r['id'],'text':str(field(r,text_field,''))} for r in valid])
        operators=args.get('operators',[{'clean_html_mapper':{}},{'clean_links_mapper':{}},{'text_length_filter':{'min_len':1,'max_len':100000}}])
        try:import yaml
        except ImportError:raise ValueError('Install benchforge[production] for YAML configuration')
        recipe={'project_name':'benchforge-cleaning','dataset_path':str(input_path),'export_path':str(output_path),'np':1,'process':operators}
        path=directory/'process.yaml';path.write_text(yaml.safe_dump(recipe,sort_keys=False),encoding='utf-8')
        # run supports a configured interpreter prefix; CLI command is data, never shell interpolation.
        run(command[-1],['--config',path],directory,config={'python_command':command[:-1]},timeout=args.get('timeout_seconds',600))
        cleaned={r['id']:r for r in rows(output_path)}
        accepted=[]
        for row in valid:
            if row['id'] not in cleaned:rejected.append({'id':row['id'],'reasons':['data_juicer_filter'],'record':row})
            else:accepted.append({**row,'cleaned_text':cleaned[row['id']]['text']})
        valid=accepted
    elif backend!='native':raise ValueError('Cleaning backend must be native or data_juicer')
    jsonl(directory/'cleaned.jsonl',valid);jsonl(directory/'invalid.jsonl',rejected)
    write(directory/'cleaning_report.json',{'input':len(data),'valid':len(valid),'invalid':len(rejected),'backend':backend,
        'labels_modified':False,'content_duplicate_detection':False})
    return {'input':str(directory/'cleaned.jsonl'),'invalid':str(directory/'invalid.jsonl'),'valid':len(valid),'rejected':len(rejected),'labels_modified':False}
