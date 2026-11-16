import path from 'node:path';

export function captureRunId(value) {
  return typeof value==='string' ? (/^(sf_[A-Za-z0-9_-]+)\.scene\d+$/.exec(value)?.[1] || value) : value;
}

function fields(value, names) {
  return Object.fromEntries(names.filter(name => value?.[name] !== undefined).map(name => [name, value[name]]));
}

// Keep diagnostics verbatim, including their object/view location. Narrative,
// inventories and saved plans are read through explicit detail/evidence tools.
function diagnostics(value, source) {
  const rows = [], diagnosticKeys = new Set(['error','errors','issues','repair_suggestions']);
  const documents = new Set(['intent','scene_brief','program','submitted_program','scene_program']);
  function visit(value, location) {
    if (!value || typeof value !== 'object') return;
    if (Array.isArray(value)) {value.forEach((item, index) => visit(item, location + '[' + index + ']'));return;}
    const row = {source:location};
    for (const [key, item] of Object.entries(value)) {
      if (diagnosticKeys.has(key) && item != null && item !== '' && (!Array.isArray(item) || item.length)) row[key] = item;
      // All false/unknown acceptance checks remain visible even when a larger
      // review wrapper carries a contradictory lifecycle success state.
      if (/(^|_)(passed|accepted|acceptable|valid|stable|renderable|allowed|approved|complete|assessed)$/.test(key) &&
          (item === false || item === null)) row[key] = item;
      if (key === 'failed' && item === true) row[key] = item;
    }
    if (Object.keys(row).length > 1) rows.push({...fields(value,['index','view','observation','reason','type']),...row});
    for (const [key, item] of Object.entries(value)) {
      if (!diagnosticKeys.has(key) && !documents.has(key)) visit(item, location + '.' + key);
    }
  }
  visit(value, source);
  return rows;
}

export function compactCaptureObservation(value, {artifacts} = {}) {
  const summary = fields(value, ['run_id','state','terminal','changed','waited_seconds','next_poll_seconds','gallery_url','watch','detail_available']);
  if (summary.terminal === undefined && ['SUCCEEDED','FAILED_FINAL','CANCELED'].includes(value.state)) summary.terminal = true;
  summary.detail_available ??= {tool:'benchforge_scene_status',...fields(value,['run_id']),detail:true};
  summary.tasks = (value.tasks || []).map(task => {
    const row = fields(task, ['id','state','phase','revision','workflow','tool','next_action','failure','queue','evidence',
      'summary','downloads','asset_files','design_reference_ready','records','diagnostics']);
    // Observe packages can have no unit at all. Assigning an absent optional
    // field creates undefined, which the native tool transport rejects before
    // the caller can see the otherwise valid task status.
    for (const key of ['phase','revision','workflow']) {
      if (row[key] == null && task.unit?.[key] !== undefined) row[key] = task.unit[key];
    }
    // Quality failures contain the same complete scene review in failure and
    // result. Keep failure identity and actions, project its known review
    // wrapper, and retain every diagnostic below. Full evidence stays on disk.
    if (task.failure?.scene_review) row.failure = {...task.failure,
      scene_review:fields(task.failure.scene_review,['passed','acceptable','assessed','status','issues','errors','repair_suggestions','publication_status'])};
    if (task.progress) {
      row.progress = fields(task.progress, ['stage','phase','state','status','percent','percentage','progress_percent',
        'completed','total','current','frames_done','frames_total','objects_done','objects_total','updated_ns',
        'age_seconds','elapsed_seconds','interrupted','scene_acceptance','error','errors','next_action']);
      if (row.progress.scene_acceptance && typeof row.progress.scene_acceptance === 'object') {
        row.progress.scene_acceptance = fields(row.progress.scene_acceptance, ['passed','accepted','allowed','status','source_path','error','errors','issues']);
      }
    }
    if (task.quality) {
      row.quality = fields(task.quality, ['scene_quality_passed','visual_realism','interaction_passed','interaction_status',
        'publication_status','data_export_complete','data_target_reached','collection_acceptance','human_approved','benchmark_approved']);
      if (task.quality.feedback) row.quality.feedback = fields(task.quality.feedback, ['file','issues','repair_suggestions']);
    }
    const result = task.result;
    const scene = !row.workflow || row.workflow === 'scene';
    if (result && scene) {
      row.result = fields(result, ['operation','directory','scene_quality_assessed','data_exported','source_path']);
      if (result.report) row.result.report = fields(result.report, ['status','frames','renderable','errors','source_path','available']);
      if (result.views) row.result.views = result.views.map(view => {
        const image = fields(view, ['frame_id','source_path','metadata_path','visible_objects']);
        if (view.camera) image.camera = fields(view.camera, ['position','target']);
        return image;
      });
      const problems = [...diagnostics(task.failure, 'failure'), ...diagnostics(task.progress, 'progress'), ...diagnostics(result.scene_review, 'result.scene_review'),
        ...diagnostics(result.report, 'result.report')];
      if (problems.length && !row.diagnostics) row.diagnostics = problems;
    } else {
      // Asset/layout/generation tools retain their operation-specific outputs.
      if (result !== undefined) row.result = result;
      if (task.design !== undefined) row.design = task.design;
      const problems = [...diagnostics(task.failure, 'failure'), ...diagnostics(task.progress, 'progress')];
      if (problems.length && !row.diagnostics) row.diagnostics = problems;
    }
    if (artifacts && /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(task.id || '') && Number.isInteger(row.revision) && row.revision >= 0) {
      const folder = path.join(artifacts, task.id, 'revision_' + row.revision);
      row.records = {...row.records, directory:folder};
      if (task.progress) row.records.progress_path = path.join(folder, 'progress.json');
      if (task.quality || result?.scene_review) row.records.scene_review_path = path.join(folder, 'scene_review.json');
      if (result?.report) row.records.capture_report_path = result.report.source_path || path.join(folder, 'capture','report.json');
      if (result?.views?.length) row.records.capture_program_path = path.join(folder, 'capture','program.json');
      if (task.summary) row.records.complete_path = path.join(folder, 'release','complete.json');
    }
    return row;
  });
  return summary;
}
