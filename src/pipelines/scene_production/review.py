import json
from urllib.error import HTTPError
from storage.files import write_json
from service_access.openai_client import completion
from capabilities.dataset_authoring.scene_capture.products import produce
from capabilities.scene_authoring.validation.quality import audit_scene
from service_access.progress import record_progress
from capabilities.scene_authoring.geometry.preview import compact_preview
from capabilities.scene_authoring.validation.acceptance import capture_acceptance
from capabilities.scene_authoring.validation.review_reference import review_spatial_context
from capabilities.scene_authoring.validation.review_reference import GUIDANCE as REFERENCE_GUIDANCE
from capabilities.scene_authoring.validation.review_reference import FACING_GUIDANCE
from capabilities.scene_authoring.contracts.requirements import scene_requirements



from pipelines.scene_production.planning import _strict_policy
from pipelines.scene_production.repair_progress import capture_ref, comparison_prompt, load_baseline, repair_decision

# 检查采集报告、真实证据与场景质量，保存验收回执或修复反馈并响应取消。
def review_scene(store,task,stop,*,capture_only=False):
    if stop.is_set():
        if getattr(stop, 'canceled', False):
            store.update(task['id'],state='CANCELED',failure={'stage':'review','error':'run canceled'})
        return
    unit=task['unit'];directory=store.directory(task['id'],unit['revision']);capture=directory/'capture'
    execution_mode=unit.get('intent',{}).get('execution_mode')
    record_progress(directory,'scene_review')
    report=json.loads((capture/'report.json').read_text())
    program=json.loads((directory/'program.json').read_text())
    evidence=json.loads((capture/'evidence.json').read_text()) if (capture/'evidence.json').is_file() else {}
    strict_policy = _strict_policy(store.root, execution_mode)
    physical_precheck=capture_acceptance(store.root,program,report,evidence,{},capture,execution_mode) if strict_policy else {}
    physical_issues=physical_precheck.get('physical_issues',[])
    repair_base=load_baseline(store,task,physical_precheck)
    spatial_context=None
    if report['status']=='captured' and any(capture.glob('view_*.png')):
        prompt='检查实际 Isaac 视图和执行证据是否满足原始场景需求：'+json.dumps(unit['intent'],ensure_ascii=False)+'''。
返回 JSON：{"acceptable":boolean,"visual_quality":1到5,"semantic_fidelity":1到5,"issues":[string],"repair_suggestions":[string],"visual_realism":{"status":"needs_improvement"|"visually_plausible"|"matches_declared_geometry"|"not_requested","issues":[string],"prioritized_repairs":[string],"basis":"rendered_views_only"}}。
依据任务判断物体身份、尺度、支撑、功能分区、材质、采光和机位。逼真室内要求还应检查建筑围合、连接细节、间接光、接触阴影及亮部纹理；明确可见的实质缺陷应影响 acceptable。每项问题标出对应视图或执行记录，建议具体的资产、程序或采集调整。
执行回执说明实际使用的资产、尺寸、姿态和材质；视觉评价说明是否可辨识且符合任务，两者分别陈述。采用最终世界姿态和实际尺寸；组合体的 size 是整体布局包络，geometry_parts 记录执行器实际创建的局部零件及 USD 类型，零件尺寸和偏移来自执行程序。区分几何构成与图片中的可辨识程度。AABB 是保守包围盒，预览警告不能单独证明穿透。材质 applied/skipped、纹理 fallback、未校准参数按回执解释。
physics 是动作前沉降；interaction 是实际施力轨迹，view_N 是动作结束后的视图，动作前后另有独立图片。建议采用已支持的程序字段；能力缺口直接说明。
当前是采集后的场景审查，数据生成和打包尚未执行，将在本阶段之后进行。target_items 是后续题目导出目标，不是当前物体或标注数量；最终包和导出计数此时缺失不能作为场景不合格理由。可据当前图像、分割和动作证据指出影响后续数据可用性的具体问题；导出是否成功由后续实际产物验证。
模型意见不写入 GT，也不等同人审、照片真实感认证或正式 benchmark 通过。
'''
        prompt+='\n沉降记录：'+json.dumps(report.get('physics',{}),ensure_ascii=False)
        prompt+='\n本次已知物理/准入问题：'+json.dumps(physical_issues,ensure_ascii=False)
        prompt+=('\n一次汇总全部机位中可确认的独立问题，并标明对象、视图和证据；'
                 '已有物理失败不应阻止检查其它物体的可见外观、朝向和布局。'
                 '每项单独判断，不能因一个物体失败而把其它物体也判失败。'
                 '物理回执、静态包围盒疑点和视觉意见分开报告；建议将有关联的问题成组修复后统一采集。')
        if strict_policy:
            prompt+='''\n严格生产验收：默认要求真实可辨识的场景资产和符合生活常识的布局。简化几何只有保存计划明确声明的实验范围才可合格，不能以几何形状冒充真实家具。逐一检查物体身份、正常尺度、支撑与安装、房间用途与物品关系、通行与操作空间、材质与照明。任何不确定、缺图或问题必须 passed=false；不得仅凭名字、设计图或全静态 stable=true 宣称物理合理。额外返回 life_review:{checks:[{check:object_identity|realistic_scale|support_and_attachment|functional_layout|access_and_clearance|materials_and_lighting,passed:boolean,evidence:[{view:"view_0.png",observation:"该实际视图中的具体可见现象"}]}]}。六项分别恰好一条，每条给出真实已附图片文件名和具体观察；不合格给出可执行修改。visual_realism.status 必须根据实拍作出判断。模型意见不是物理测量或GT。'''
        if strict_policy:
            admission = physical_precheck
            if admission.get('scene_brief'):
                brief=admission['scene_brief']
                context={k:brief[k] for k in ('name','summary','object_roles','physical_assumptions')}
                context['scene_requirements']=scene_requirements(brief)
                if brief.get('spatial_reference'):context['spatial_reference']=brief['spatial_reference']
                spatial_context=review_spatial_context(brief,evidence,physical_precheck.get('native_relation_checks',[]))
                write_json(directory/'scene_spatial_context.json',spatial_context)
                prompt+='\n明确参照和本次原生方向/支撑事实（不能代替实际逐图生活检查）：'+json.dumps(spatial_context,ensure_ascii=False)
                context['visual_relations']=[{'index':i,**r} for i,r in enumerate(brief['relations']) if r['type'] not in {'supported_by','mounted_to'}]
                if any(r['type'] == 'facing' for r in context['visual_relations']):
                    prompt += FACING_GUIDANCE
                prompt += '\n必须对照的场景标题、摘要、角色及空间关系：' + json.dumps(context, ensure_ascii=False)
                prompt += ('\n额外返回 brief_alignment:{passed:boolean,hard_requirements_preserved:boolean,observations:[{view:"view_0.png",observation:"实际可见依据"}],'
                           'unresolved:[string],proposed_summary_revision:{reason:string,summary:string,changes:[string]}}。'
                           '检查摘要中的每个关键物体角色、支撑、邻接、朝向、取用与通行关系。功能布局须适合人在此实际使用。'
                           '对照全部机位发现椅子翻倒、柜门冲突、桌面穿物、悬空、失衡、比例扭曲或不合用途摆放均须失败。'
                           '摘要有遗漏或自身矛盾时提出具体修订，保留用户硬要求；不能通过改写摘要为已有缺陷开脱。'
                           '修订建议只是待执行设计，未重新采集验收不得把它当成修复证据。')
                prompt += ('\nbrief_alignment 另须完整返回 requirement_checks 与 relation_checks 数组：'
                           '分别按 scene_requirements 和 visual_relations 的显式index逐项检查，每条 {index:整数,passed:boolean,view:"view_0.png",observation:"具体可见依据"}。'
                           '当前只验收S2场景。摘要或任务中提到的S3轨迹数量、逐帧关节读数、标注及S4题目、S5打包，留给对应阶段验证；不得因为尚未采集而判场景失败。机器人资产身份、布局和可执行条件仍必须在S2检查。'
                           '不得遗漏、重复或仅笼统声称全部通过。实际图无法确认的要求应失败并留在unresolved。')
                prompt += '\n支撑和固定安装已按原生回执逐项校验并单独留证；不重复输出物理relation_checks。仍须检查图中可见的悬空、穿插和姿态缺陷，不能根据物体名字认为合格。'
                prompt += '\nbrief_alignment.observations 须逐一覆盖本次全部 view_N.png，每个视角写具体观察，不能只挑最好的一张。'
            declaration = admission.get('geometry_exception')
            if declaration:
                prompt += '\n保存计划声明的简化几何实验：' + json.dumps(declaration, ensure_ascii=False)
                prompt += ('\n按该声明检查几何身份、测量尺度、支撑与安装、实验布局、通行/操作空间和材质照明；'
                           '生活合理性采用声明的实验设定，不要求实验方块长得像日常家具。'
                           '六项逐图检查仍须通过，例外不替代物理证据。实拍符合声明时 visual_realism.status=matches_declared_geometry；'
                           '不符合时 needs_improvement 且 acceptable=false。不得将此实验标为真实生活场景。')
        prompt+='\n渲染执行记录：'+json.dumps(report.get('render_settings',{}),ensure_ascii=False)
        entity_fields={'shadow_settings','render_representation','appearance_scope','id','kind','label','prim_path','size','requested_size','initial_center','settled_center','final_center','current_center','declared_yaw_deg','world_transform','world_transform_convention','spatial_evidence_step','orientation_wxyz','world_aabb','support','dynamic','parameter_origin','physics','collision','appearance','geometry_parts'}
        dependency_fields={'appearance_scope','render_representation','gaussian','entity_id','kind','asset_id','asset_kind','embedded_geometry','vertices','triangles','source_frame','world_frame','source_up_axis','normalization','normalization_scale_xyz','uniform_scale','scale_mode','requested_size_limits_m','actual_size_m','source_bounds','oriented_bounds_before_scale','orientation_deg_xyz','orientation_source','source_watertight','appearance','appearance_overrides','transported_materials','transported_uvs','bound_texture_materials','texture_sampling','native_colliders_preserved','portability'}
        material_fields={'appearance_scope','entity_id','prim_path','kind','texture_id','texture_tint','uv_scale_m','color_space','normal_convention','calibration','source','roughness','metallic','applied','skipped'}
        receipt={'origin':evidence.get('origin'),'entities':[{key:value for key,value in entity.items() if key in entity_fields} for entity in evidence.get('entities',{}).values() if isinstance(entity,dict)],'asset_dependencies':[{key:value for key,value in dependency.items() if key in dependency_fields} for dependency in evidence.get('asset_dependencies',[]) if isinstance(dependency,dict)],'material_dependencies':[{key:value for key,value in dependency.items() if key in material_fields} for dependency in evidence.get('material_dependencies',[]) if isinstance(dependency,dict)],'realism':evidence.get('realism',{})}
        if strict_policy:
            # Full native receipts remain saved and are checked deterministically.
            # Repeating all material paths and primitive parts in a visual prompt
            # makes complex rooms time out without adding visible observations.
            visual_fields={'id','kind','label','size','current_center','settled_center','final_center',
                           'world_aabb','support','dynamic','orientation_wxyz','parameter_origin'}
            receipt={'origin':evidence.get('origin'),
                     'entities':[{k:v for k,v in entity.items() if k in visual_fields} for entity in evidence.get('entities',{}).values()],
                     'asset_dependencies':[{k:v for k,v in dependency.items() if k in {'entity_id','asset_id','actual_size_m','orientation_deg_xyz','render_representation','scale_mode'}}
                                           for dependency in evidence.get('asset_dependencies',[])],
                     'scope':'compact visual context; full collider, part, support and material receipts remain in capture/evidence.json and acceptance checks'}
            prompt+='\n紧凑返回：每条观察只写一个具体可见事实（约20到45个汉字），不重复摘要；不确定仍须失败。只返回规定字段，禁止长篇重述程序。'
        prompt+='\n实体与资产执行记录：'+json.dumps(receipt,ensure_ascii=False)
        geometry_path=directory/'geometry_preview.json'
        changes_path=directory/'repair_changes.json'
        if changes_path.is_file():
            changes=json.loads(changes_path.read_text())
            changes={**changes,'comparison_recorded_at':'before_capture','capture_status':report['status'],
                     'capture_token':report.get('token')}
            prompt+='\n本次程序改动对照：'+json.dumps(changes,ensure_ascii=False)
        if geometry_path.is_file() and not strict_policy:
            prompt+='\n仿真前的程序几何建议：'+json.dumps(compact_preview(
                json.loads(geometry_path.read_text()), full_report=str(geometry_path)),ensure_ascii=False)
        camera_receipts=[{'frame_id':frame.get('frame_id'),'image':frame.get('image'),'camera':frame.get('camera'),'step_id':frame.get('step_id')} for frame in evidence.get('frames',[])]
        prompt+='\n相机执行记录：'+json.dumps(camera_receipts,ensure_ascii=False)
        prompt+='\n实际外力动作证据：'+json.dumps(evidence.get('interaction',{}),ensure_ascii=False)
        prompt+='\n环境贴图执行记录：'+json.dumps(evidence.get('environment_dependencies',[]),ensure_ascii=False)
        prompt+='\n灯具尺寸执行记录：'+json.dumps(evidence.get('light_receipts',[]),ensure_ascii=False)
        review_images=sorted(capture.glob('view_*.png'))
        for action_result in evidence.get('interaction',{}).get('action_results',[]):
            for key in ('before_image','after_image'):
                name=action_result.get(key)
                if name and (capture/name).is_file():review_images.append(capture/name)
        prompt+='\n实际图片顺序：'+json.dumps([image.name for image in review_images],ensure_ascii=False)
        prompt+='\n实拍相机位置、朝向与图片对应：'+json.dumps([{'view':f'view_{i}.png',**c} for i,c in enumerate(program['cameras'])],ensure_ascii=False)
        prompt+='\n关系判断须区分画面坐标、世界坐标及使用者视角；左右前后按摘要明确参照判断，不得随相机换位而改变场景关系。未定义参照时指出文字歧义，不臆造世界方向。相机高度以执行位置为准，不能仅凭俯视构图判断拍摄高度。外观名称须由实际形态和材质支持，禁止把未观察到的颜色或品牌当事实。'
        prompt+='\n全部机位共同覆盖场景，不要求每张照片同时看见所有物体。单一视角遮挡不能证明物体缺失、穿插或悬空；须检查其它对应机位和原生姿态证据。全部机位仍无法辨认或验证的项目应失败并列为未解；清晰可见的缺陷必须保留，不能由程序名称替代外观。'
        prompt+='\n'+REFERENCE_GUIDANCE
        layout_reference=directory/'layout_reference.png'
        if layout_reference.is_file():
            review_images.append(layout_reference)
            prompt+=f'\n第 {len(review_images)} 张是扩散设计参考。额外返回 design_alignment:{{status:"aligned"|"partial"|"diverged",issues:[string]}}，比较功能分区、主要物体、材质、采光和氛围；参考仅用于设计比较，不是仿真成品、GT 或像素匹配目标。'
        def assess(prompt_text, images):
            """沿用当前评审的模型预算、取消信号及恢复记录。"""
            return completion(prompt_text,directory/'model_calls'/'scene_review',images,
                              10000 if strict_policy.get('require_scene_brief') else 5000,
                              bounded_review=bool(strict_policy),stop=stop,context=unit.get('run_context'))

        if repair_base and repair_base.get('images'):
            try:
                review=assess(prompt+comparison_prompt(repair_base,physical_precheck),review_images+repair_base['images'])
            except HTTPError as error:
                if error.code not in (400,413,422):
                    raise
                # 部分模型服务按图片数或上下文长度拒绝请求，此时仍须验收当前场景。
                review=assess(prompt,review_images)
        else:
            review=assess(prompt,review_images)
    else: review={'acceptable':False,'issues':physical_issues or report.get('errors',[]),
                 'repair_suggestions':['Repair the actual support, attachment or layout issue in a new complete program and capture again.'],
                 'visual_review_skipped':True,
                 'visual_review_skip_reason':'native scene views are unavailable'}
    if spatial_context is not None:review['spatial_reference_context']=spatial_context
    review['physical_stable']=report.get('physics',{}).get('stable',False);review['renderable']=report.get('renderable',False)
    frames=evidence.get('frames',[])
    native=evidence.get('origin')=='isaac_native'
    views=[frame for frame in frames if isinstance(frame,dict) and isinstance(frame.get('frame_id'),str) and (capture/(frame['frame_id']+'.png')).is_file()]
    quality_capture={**report,'views':views,'depth':[],'segmentation':{},'semantic_labels':{},'interaction':evidence.get('interaction',{})}
    for frame in views:
        name=frame['frame_id']
        if (capture/(name+'_depth.npy')).is_file():quality_capture['depth'].append(name+'_depth.npy')
    segmented=[frame for frame in views if (capture/(frame['frame_id']+'_semantic.npy')).is_file() and (capture/(frame['frame_id']+'_instance.npy')).is_file()]
    if native and segmented:
        labels={'authority':'simulator','frames':[{'frame_id':frame['frame_id'],'objects':frame.get('objects',[]),'semantic_info':frame.get('semantic_info',{}),'instance_info':frame.get('instance_info',{})} for frame in segmented]}
        quality_capture['segmentation']=labels;quality_capture['semantic_labels']=labels
    if isinstance(evidence.get('physics'),dict):quality_capture['physics']=evidence['physics']
    review['scene_quality']=audit_scene(program,quality_capture,unit['intent'])
    requested_actions=[action for action in program.get('interactions',program.get('affordances',[])) if action.get('action')=='apply_force']
    interaction=evidence.get('interaction',{})
    review['interaction']=interaction
    results={row.get('id'):row for row in interaction.get('action_results',[]) if isinstance(row,dict)}
    # 核对施力动作的成功身份及轨迹、前后图像回执文件均真实存在。
    def action_has_receipt(action):
        result=results.get(action['id'],{})
        return (result.get('success') is True and result.get('action')=='apply_force' and result.get('object_id')==action['object_id']
                and all(isinstance(result.get(key),str) and (capture/result[key]).is_file() for key in ('trajectory_file','before_image','after_image')))
    interaction_passed=(native and interaction.get('validated') is True and all(action_has_receipt(action) for action in requested_actions)) if requested_actions else None
    if interaction_passed is False:
        review.setdefault('issues',[]).append('Requested physical interaction did not pass simulator validation')
        review.setdefault('repair_suggestions',[]).append('Repair action force, duration, surface clearance or dynamic support using the captured action trace')
    review['interaction_requested']=bool(requested_actions)
    review['interaction_passed']=interaction_passed
    acceptance=capture_acceptance(store.root,program,report,evidence,review,capture,execution_mode)
    if acceptance.get('enforced'):
        review['production_acceptance']=acceptance
        write_json(directory/'scene_acceptance.json',acceptance)
        if acceptance.get('scene_brief'):
            write_json(directory/'scene_brief_alignment.json', {'brief': acceptance['scene_brief'],
                       'observation': review.get('brief_alignment', {}), 'passed': acceptance['passed'],
                       'iteration_required': not acceptance['passed'], 'hard_requirements_may_be_relaxed': False})
        review['issues']=list(dict.fromkeys([*review.get('issues',[]),*acceptance['issues']]))
    acceptance_passed = acceptance.get('passed') is not False
    passed=review.get('acceptable') is True and review['physical_stable'] and review['renderable'] and interaction_passed is not False and acceptance_passed
    review['passed']=passed
    if repair_base:
        candidate=capture_ref(task['id'],unit['revision'],acceptance.get('capture_token'))
        review['repair_progress']=repair_decision(repair_base,candidate,acceptance,review,
            {frame['frame_id']+'.png' for frame in views})
    review['publication_status']='pilot_candidate' if passed else 'exploration_requires_improvement'
    review['execution_mode']=execution_mode or 'exploration'
    review['evaluation_status']='not_evaluated' if not acceptance.get('enforced') else ('passed' if acceptance.get('passed') else 'failed')
    write_json(directory/'scene_review.json',review)
    if not passed:
        if acceptance.get('enforced'):
            # Strict production never exports exploration/failed scenes. DSH can
            # read these receipts and submit a revised, fully checked program.
            record_progress(directory,'scene_acceptance_failed',issues=review.get('issues',[]))
            store.update(task['id'],state='FAILED_FINAL',failure={'scene_review':review,'artifacts_preserved':str(directory),
                'next_action':'repair_complete_program_and_resubmit','data_exported':False},result={'directory':str(directory)})
            return
        # The operator owns revisions of a submitted SceneProgram. Keep its
        # capture/review and export eligible observations without a second planner.
        if not unit.get('submitted_program') and unit.get('quality_repairs',0)<2 and report['status']=='captured':
            store.update(task['id'],state='PENDING',unit={**unit,'phase':'plan','revision':unit['revision']+1,'quality_source_revision':unit['revision'],'quality_repairs':unit.get('quality_repairs',0)+1})
            return
        if report['status']!='captured' or not review['renderable']:
            store.update(task['id'],state='FAILED_FINAL',failure={'scene_review':review,'artifacts_preserved':str(directory),'next_action':'engineering_recovery'},result={'directory':str(directory)})
            return
        # Exploration keeps useful observations even when scene-wide aesthetics or
        # dynamics still need work; product eligibility records the actual evidence.
    if store.snapshot(task['run_id'])['state']=='CANCELED':return
    if capture_only:
        record_progress(directory,'complete',scene_acceptance='passed')
        store.update(task['id'],state='SUCCEEDED',result={'operation':'capture_only','directory':str(directory),
            'report':report,'scene_review':review,'scene_quality_assessed':True,'data_exported':False},unit={**unit,'phase':'complete'})
        return
    record_progress(directory,'data_export')
    summary=produce(directory,unit['intent'].get('split','dev'),unit['intent'],stop=stop,context=unit.get('run_context'))
    record_progress(directory,'complete')
    store.update(task['id'],state='SUCCEEDED',result={'summary':summary,'directory':str(directory),'scene_review':review},unit={**unit,'phase':'complete'})
