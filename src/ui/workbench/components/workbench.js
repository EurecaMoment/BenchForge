export function createWorkbenchComponent({React, helpers, components}) {
  const h = React.createElement;
  const {status, button, card, empty, json, sceneName, questionText, planValue, planMarkdown,
  planMarkdownView, planEditInstruction, table, select, initialState, productsResourceAddress,
  displayRef, filteredPage, useRecord: useWorkbenchRecord, usePanelVisible, lifecycle,
  productionProductsRevision, sceneStage, pipelineForStage, sceneForStage, progressSceneForStage,
  registeredCollectionProgress, currentGeneration, sceneCollectionProgress, annotationProducts,
  stageState, recordedStageState,
  STAGES, PLAN_SECTIONS, PLAN_FIELDS, styles, dictionaries} = helpers;
  const {TemplateCatalog, RunDetails, executionStage, reportedProgress, progressAmount, ParallelProgress,
    StageProgress, PublicObservation, QuestionDialog, firstImage, sceneProjects, SceneGallery, SceneVideo,
    SceneLibrary, PreviewDialog, ArtifactGallery, StageReviewPanel, PlanProduct, ReportProduct, Quality} = components;
  function Workbench({t, sessionId, useResource, useSessions, useSession, useInput, inputActions, canSubmit = false,
    stopExecution, requestWorkbench, useStore, actions, configureProduction}) {
    const useRecord = (params, revision, interval, active) =>
      useWorkbenchRecord(requestWorkbench, params, revision, interval, active);
    const panelRef = React.useRef(null);
    const visible = usePanelVisible(panelRef);
    const {route, demand} = useStore((value) => value);
    const [revision, setRevision] = React.useState(0);
    const [sample, setSample] = React.useState(null);
    const [search, setSearch] = React.useState(route.q);
    const [notice, setNotice] = React.useState('');
    const [catalogQuery, setCatalogQuery] = React.useState('');
    const [catalogPage, setCatalogPage] = React.useState(1);
    const [capability, setCapability] = React.useState('');
    const [preview, setPreview] = React.useState(null);
    const [sending, setSending] = React.useState(false);
    const [demandError, setDemandError] = React.useState('');
    const [sceneQuery, setSceneQuery] = React.useState('');
    const [sceneOrigin, setSceneOrigin] = React.useState('all');
    const [scenePage, setScenePage] = React.useState(1);
    const [sceneChoice, setSceneChoice] = React.useState(null);
    const [sceneSaving, setSceneSaving] = React.useState(false);
    const [sceneError, setSceneError] = React.useState('');
    const sceneSaveLock = React.useRef(false);
    const selectionSubmitLock = React.useRef(false);
    const lastSceneLibrary = React.useRef(null);
    const submitLock = React.useRef(false);
    const pendingSubmission = React.useRef(null);
    const lastSubmitted = React.useRef('');
    const inputState = useInput((state) => state);
    const sessionState = useSession((state) => state);
    const update = actions.updateRoute;
    const workspacePath = useSessions((state) => state.byId[sessionId]?.cwd || '');
    const productResource = useResource(productsResourceAddress(sessionId, workspacePath));
    const conversationProductItems = (productResource.value?.items || []).filter((row) => row.status === 'available')
      .map((row) => ({...row, file: row.name}));
    const unassignedProductItems = conversationProductItems.filter((row) => !row.stage);
    const unavailableProductCount = (productResource.value?.items || []).filter((row) => row.status !== 'available').length;
    const conversationRunning = sessionState.running;
    const attachments = inputState.attachmentIds || [];
    const hasComposerContent = Boolean(inputState.draft || attachments.length);
    const progress = useRecord(workspacePath ? {view: 'progress', workspace_path: workspacePath, session_id: sessionId} : null, revision, 3000, visible);
    const receiptsRevision = revision + ':' + productionProductsRevision(progress.value);
    const summary = useRecord(workspacePath ? {view: 'summary', workspace_path: workspacePath, session_id: sessionId} : null, receiptsRevision, 30000, visible);
    const rows = summary.value?.runs || [];
    const pipelines = progress.value?.pipelines || [];
    const explicitRunId = rows.find((row) => row.id === route.run)?.id || pipelines.find((row) => row.id === route.run)?.id;
    const selectedRunId = route.production && !route.run ? '' : explicitRunId ||
      pipelineForStage(route.stage, pipelines)?.id || (['S2', 'S3'].includes(route.stage) ? '' : rows[0]?.id || pipelines[0]?.id || '');
    const datasetRunId = route.production && !route.run ? '' : explicitRunId || pipelineForStage('S4', pipelines)?.id || selectedRunId;
    const datasets = (summary.value?.datasets || []).filter((row) => datasetRunId ? row.run_id === datasetRunId : !row.run_id);
    const selectedDataset = datasets.find((row) => row.id === route.dataset);
    const sceneJobs = progress.value?.scenes?.runs || [];
    const scene = route.production ? sceneJobs.find((row) => row.run_id === route.production) :
      sceneForStage(route.stage, sceneJobs) || sceneForStage('S2', sceneJobs);
    const sourceScene = selectedRunId ? null : scene;
    const run = useRecord(workspacePath && selectedRunId ? {view: 'run', run: selectedRunId, workspace_path: workspacePath, session_id: sessionId} : null, receiptsRevision, 10000, visible);
    const automaticScene = !route.run && !route.production ? sceneForStage('S2', sceneJobs) : null;
    const artifactScene = sourceScene?.run_id || automaticScene?.run_id || run.value?.scene_ids?.[0];
    const artifactRunId = sourceScene || automaticScene ? '' : selectedRunId;
    const sceneArtifacts = useRecord(workspacePath && artifactScene ?
      {view: 'stage_artifacts', scene: artifactScene, run: artifactRunId, session_id: sessionId, workspace_path: workspacePath} : null, receiptsRevision, 10000, visible);
    const catalog = useRecord(route.stage === 'S1'
      ? {view: 'catalog', q: catalogQuery, capability, page: catalogPage, page_size: 12} : null, revision);
    const dataset = useRecord(workspacePath && ['S4', 'S5'].includes(route.stage) && selectedDataset ?
      {view: 'dataset', dataset: route.dataset, template: route.template, split: route.split, q: route.q, page: route.page, page_size: route.size, workspace_path: workspacePath, session_id: sessionId} : null, receiptsRevision, 30000, visible);
    const sceneLibrary = useRecord(workspacePath ? {view: 'scene_library', workspace_path: workspacePath, session_id: sessionId,
      q: sceneQuery, origin: sceneOrigin, page: scenePage} : null, receiptsRevision, 30000, visible);
    const planSceneLibrary=useRecord(workspacePath && route.stage==='S1' ? {view:'scene_library',workspace_path:workspacePath,session_id:sessionId,
      origin:'selected',page_size:24} : null,receiptsRevision,30000,visible);
    const reviewRecord = useRecord(workspacePath ? {view:'stage_review',workspace_path:workspacePath,session_id:sessionId} : null,revision,3000,visible);
    const reviewAction = async (action,instruction='') => {
      const reviewId = reviewRecord.value?.review_id;
      const value = await requestWorkbench({view: 'stage_review', workspace_path: workspacePath, session_id: sessionId},
        {body: {action, ...(reviewId ? {review_id: reviewId} : {}), instruction}});
      if (value.error) throw new Error(value.error);
      setRevision(old => old+1);
      return value;
    };
    if (sceneLibrary.value) lastSceneLibrary.current = sceneLibrary.value;
    const sceneLibraryView = sceneLibrary.value ? sceneLibrary : {...sceneLibrary, value: lastSceneLibrary.current};
    const savedSceneSelection = sceneLibraryView.value?.selection;
    const currentSceneSelection = sceneChoice && (!savedSceneSelection || sceneChoice.version >= savedSceneSelection.version)
      ? sceneChoice : savedSceneSelection;
    const chooseScenes = async (selected, allow_new_scenes) => {
      if (sceneSaveLock.current || selectionSubmitLock.current || !currentSceneSelection) return;
      sceneSaveLock.current = true; setSceneSaving(true); setSceneError('');
      setSceneChoice({...currentSceneSelection, selected, allow_new_scenes});
      try {
        const value = await requestWorkbench({view: 'scene_selection', workspace_path: workspacePath, session_id: sessionId},
          {body: {version: currentSceneSelection.version, selected, allow_new_scenes}});
        if (value.error) throw new Error(value.error);
        setSceneChoice(value);
      } catch (error) {setSceneChoice(currentSceneSelection); setSceneError(t('sceneSaveFailed') + ' ' + error.message);}
      finally {sceneSaveLock.current = false; setSceneSaving(false);}
    };
    const sampleId = sample?.row_index;
    const currentSample = sample ? dataset.value?.items.find((row) => row.row_index === sampleId &&
      (row.item_id || row.id) === (sample.item_id || sample.id)) : null;
    React.useEffect(() => { setSample(null); }, [route.dataset, route.page, route.size, route.template, route.split, route.q]);
    React.useEffect(() => {setPreview((current) => current?.library ? current : null);}, [selectedRunId, sourceScene?.run_id]);
    React.useEffect(() => {
      if (dataset.value && route.page > Math.max(1, dataset.value.pages)) update({page: Math.max(1, dataset.value.pages)});
    }, [dataset.value, route.page]);
    React.useEffect(() => {
      if (sample && dataset.value && !currentSample) {setSample(null); setNotice(t('sampleRemoved'));}
    }, [dataset.value, sample, currentSample]);
    React.useEffect(() => {
      if (route.stage !== 'S4') setSample(null);
    }, [sampleId, route.stage]);
    React.useEffect(() => {
      if (!summary.value) return;
      const patch = {};
      if (route.run && !rows.some((row) => row.id === route.run)) patch.run = '';
      if (!selectedDataset && (route.dataset || datasets.length)) Object.assign(patch, {dataset: datasets[0]?.id || '', template: '', split: '', q: '', page: 1});
      if (Object.keys(patch).length) update(patch);
    }, [summary.value, selectedRunId, route.run, route.dataset]);
    const runListing = filteredPage(rows, route.runFilter, route.runListPage, route.runListSize,
      (row) => [row.label, row.id, row.state].join(' '));
    const versionListing = filteredPage(datasets, route.versionFilter, route.versionListPage, route.versionListSize,
      (row) => [row.workspace, row.version, row.run_id, row.state].join(' '));
    const stage = STAGES.find((step) => step.id === route.stage) || STAGES[0];
    const pipeline = pipelines.find((row) => row.id === selectedRunId);
    const allStageFiles = [...(sourceScene ? [] : run.value?.stage_artifacts || []), ...(sceneArtifacts.value?.items || []),
      ...conversationProductItems];
    const stageFiles = allStageFiles.filter((row) => row.stage === stage.id).map((row) => ({...row,
      image_url: row.stage === 'S3' && row.kind === 'json' ? allStageFiles.find((image) => image.kind === 'image' &&
        image.task_id === row.task_id && image.revision === row.revision && image.file === row.file?.replace(/\.json$/, '.png'))?.url : undefined}));
    const activePipeline = sourceScene ? null : pipeline;
    const navigationPipeline = (id) => !route.run && !route.production
      ? pipelineForStage(id, pipelines) || (['S2', 'S3'].includes(id) ? null : activePipeline) : activePipeline;
    const navigationScene = (id) => route.run || route.production ? sourceScene :
      navigationPipeline(id) ? null : sceneForStage(id, sceneJobs);
    const prepare = (text = '') => {
      if (typeof inputActions?.submit !== 'function') {setNotice(t('noSendInterface')); return;}
      if (typeof text === 'string' && text.trim()) {
        const span = inputActions.captureInsertion();
        if (!inputActions.insertText(text + '\n', {...span, end: span.start})) {setNotice(t('draftBusy')); return;}
      }
      try {configureProduction({...demand, mode: 'append'}, true);}
      catch (error) {setNotice(error.message); return;}
      actions.requestChange();
      setNotice(t('continueInComposer'));
    };
    const submitRequest = (payload, setError) => {
      if (submitLock.current || payload.submitted || lastSubmitted.current === payload.text) return false;
      if (!canSubmit) {setError(t('permissionsPending')); return false;}
      if (typeof inputActions?.submit !== 'function') {setError(t('noSendInterface')); return false;}
      if (!payload.text.trim()) {setError(t('requestRequired')); return false;}
      if (inputState.phase !== 'plain' || inputState.claim) {setError(t('sendBusy')); return false;}
      if (pendingSubmission.current?.inserted && pendingSubmission.current.text !== payload.text) {
        setError(t('previousUnsentRequest')); return false;
      }
      if (hasComposerContent && !pendingSubmission.current?.inserted) {
        prepare(); setError(t('continueInComposer')); return false;
      }
      payload = pendingSubmission.current?.inserted ? pendingSubmission.current : payload;
      submitLock.current = true; setSending(true); setError('');
      try {
        if (!payload.inserted) {
          const span = inputActions.captureInsertion();
          if (!inputActions.insertText('\n' + payload.text + '\n', {...span, end: span.start})) {
            setError(t('draftBusy')); return false;
          }
          payload.inserted = true;
          pendingSubmission.current = payload;
        }
        inputActions.submit();
        payload.submitted = true;
        lastSubmitted.current = payload.text;
        pendingSubmission.current = null;
        setNotice(t('sent'));
        return true;
      } catch (error) {
        setError(t('sendFailed') + ' ' + error.message);
        return false;
      } finally {submitLock.current = false; setSending(false);}
    };
    const submitWithScenes = async (payload, setError) => {
      if (selectionSubmitLock.current || submitLock.current || sceneSaveLock.current) return false;
      if (!canSubmit) {setError(t('permissionsPending')); return false;}
      if (hasComposerContent && !pendingSubmission.current?.inserted) {prepare(); setError(t('continueInComposer')); return false;}
      selectionSubmitLock.current = true; setSending(true); setError('');
      let readError = 'sceneSelectionReadFailed';
      try {
        if (!payload.reviewContinued && ['waiting_user','paused','needs_input'].includes(reviewRecord.value?.state)) {
          await reviewAction('instruction',payload.text);
          if (conversationRunning) await stopExecution();
          payload={...payload,text:'阶段等待中的用户指令已保存。先读取 stage_review(action=status)，应用以下要求后再处理后续阶段；不要绕过等待关口。\n'+payload.text};
        }
        const value = await requestWorkbench({view: 'scene_selection', workspace_path: workspacePath, session_id: sessionId});
        if (value.error) throw new Error(value.error);
        if (currentSceneSelection && value.version !== currentSceneSelection.version) {
          setSceneChoice(value); setError(t('sceneChanged')); return false;
        }
        if (!value.sources?.length && !value.allow_new_scenes) {setError(t('chooseSceneRequired')); return false;}
        readError = 'templateReadFailed';
        const template = await requestWorkbench({view: 'plan_template', workspace_path: workspacePath, session_id: sessionId});
        if (template.schema !== 'benchforge.assessment-template/v1' || !template.assessment) {
          setError(t('templateReadFailed')); return false;
        }
        return submitRequest({...payload, text: payload.text + helpers.productionContext(value, template)}, setError);
      } catch (error) {setError(t(readError) + ' ' + error.message); return false;}
      finally {selectionSubmitLock.current = false; setSending(false);}
    };
    const savePlan=async(edit)=>{
      const value = await requestWorkbench({view: 'plan_edit', workspace_path: workspacePath, session_id: sessionId},
        {body: edit});
      if (value.error) throw new Error(value.error);
      if(value.state!=='unchanged'){update({run:'',production:''});setRevision((value)=>value+1);}
      return value;
    };
    const applyPlanEdit=async(path,setError)=>{
      if(conversationRunning)await stopExecution();
      return submitWithScenes({text:planEditInstruction(path),
        inserted:false,reviewContinued:true},setError);
    };
    const openRun = (id) => update({run: id});
    const changeDataset = (id) => { update({dataset: id, template: '', split: '', q: '', page: 1}); setSearch(''); };
    const openDataset = (id) => {changeDataset(id); update({stage: 'S4'});};
    const openSample = (item) => setSample(item);
    const readFailure = () => h('div', {className: 'bfw-empty'}, t('readFailed'), ' ', button(t('retry'), () => setRevision((value) => value + 1)));
    const runTable = (items) => items.length ? table([t('workspace'), t('runs'), t('state'), t('steps'), t('artifacts'), t('updated')],
      items.map((item) => [item.workspace, button(displayRef(item.label), () => openRun(item.id), {title: item.label}), status(item.state, t), item.steps, item.artifact_total ?? '—', item.updated_at])) : empty(t('noRuns'));
    const conversationPlan = !route.run && summary.value?.plan?.selections?.some((row) => row.template_ids?.length)
      ? summary.value.plan : null;
    const plan = conversationPlan || run.value?.plan || (!selectedRunId ? summary.value?.plan : null);
    const collectionPlan = summary.value?.plan?.assessment?.scenes ? summary.value.plan : plan;
    const templateName=(id)=>plan?.template_contracts?.find(row=>row.id===id)?.title || '题型';
    const versionOptions = [['', t('chooseDataset')], ...datasets.map((row,index) =>
      [row.id,'数据集 '+(index+1)+' · '+(row.report?.accepted ?? '—')+' 题 · '+(row.updated_at || '').slice(0,10)])];
    const listControls = (listing, query, label, onQuery, onPage, pageSize, onPageSize) => h('div', {className: 'bfw-list-controls'},
      h('div', {className: 'bfw-toolbar'},
        h('input', {className: 'bfw-input', type: 'search', 'aria-label': label, placeholder: label, value: query, onChange: (e) => onQuery(e.target.value)}),
        select(t('pageSize'), String(pageSize), (value) => onPageSize(Number(value)), [12, 24, 48].map((value) => [String(value), String(value)])),
        h('span', {className: 'bfw-muted'}, t('matches') + ': ' + listing.total)),
      h('div', {className: 'bfw-pagination'},
        button(t('previous'), () => onPage(listing.page - 1), {disabled: listing.page <= 1}),
        h('span', null, t('page') + ' ' + listing.page + ' / ' + listing.pages),
        button(t('nextPage'), () => onPage(listing.page + 1), {disabled: listing.page >= listing.pages})));
    const data = dataset.value;
    const runHistory = h('details', {'data-run-history': true}, h('summary', null, t('runHistory')),
      h('small', {className: 'bfw-hint'}, t('scope')),
      h('div', {className: 'bfw-counts'}, h('span', null, t('runCount') + ': ' + (summary.value ? rows.length : '—')),
        h('span', null, t('datasetCount') + ': ' + (summary.value ? datasets.length : '—'))),
      listControls(runListing, route.runFilter, t('runFilter'),
        (value) => update({runFilter: value, runListPage: 1}), (page) => update({runListPage: page}),
        route.runListSize, (size) => update({runListSize: size, runListPage: 1})),
      summary.loading ? empty(t('loading')) : summary.error && !summary.value ? readFailure() :
        runListing.total ? runTable(runListing.items) : empty(rows.length ? t('noMatchingRuns') : t('noRuns')));
    const datasetDirectory = h('details', {'data-dataset-directory': true, open: !route.dataset},
      h('summary', null, t('directory')), h('small', {className: 'bfw-hint'}, t('datasetNote')),
      summary.loading ? empty(t('loading')) : summary.error && !summary.value ? readFailure() : datasets.length ? h(React.Fragment, null,
        listControls(versionListing, route.versionFilter, t('versionFilter'),
          (value) => update({versionFilter: value, versionListPage: 1}), (page) => update({versionListPage: page}),
          route.versionListSize, (size) => update({versionListSize: size, versionListPage: 1})),
        versionListing.total ? table([t('workspace'), t('version'), t('generationAccepted'), t('state'), t('open')], versionListing.items.map((row) =>
          [row.workspace, h('span', {className: 'bfw-code bfw-version', title: row.version}, displayRef(row.version)), row.report?.accepted ?? '—',
            status(row.state, t), button(t('browse'), () => openDataset(row.id))])) : empty(t('noMatchingVersions'))) : empty(t('noDatasets')));
    const datasetBrowser = h('div', {'data-dataset-browser': true},
      h('small', {className: 'bfw-hint'}, t('publicNote')),
      h('form', {className: 'bfw-toolbar', onSubmit: (e) => {e.preventDefault(); update({q: search, page: 1});}},
        select(t('template'), route.template, (id) => update({template: id, page: 1}), [['', t('all')], ...(data?.facets.templates || []).map((id) => [id,templateName(id)])]),
        select(t('split'), route.split, (id) => update({split: id, page: 1}), [['', t('all')], ...(data?.facets.splits || []).map((id) => [id, id])]),
        h('input', {className: 'bfw-input', 'aria-label': t('search'), placeholder: t('search'), value: search, onChange: (e) => setSearch(e.target.value)}),
        h('button', {className: 'bfw-button', type: 'submit'}, t('apply'))),
      h('div', {className: 'bfw-toolbar'},
        select(t('pageSize'), String(route.size), (size) => update({size: Number(size), page: 1}), [24, 48, 96].map((size) => [String(size), String(size)])),
        h('span', {className: 'bfw-muted'}, t('matches') + ': ' + (data?.total ?? '—') + ' / ' + t('total') + ': ' + (data?.all ?? '—'))),
      dataset.loading ? empty(t('loading')) : dataset.error && !dataset.value ? readFailure() :
        !data?.items.length ? h('div', {className: 'bfw-empty'}, t('noItems'),
          (route.template || route.split || route.q) && button(t('clearFilters'), () => changeDataset(route.dataset))) :
          h('div', {className: 'bfw-items bfw-question-grid','data-question-grid':true}, data.items.map((item) => h('button', {type: 'button', className: 'bfw-item', key: item.row_index,
            'data-sample-row': String(item.row_index),'aria-label':'查看题目 '+(item.row_index+1),onClick: () => openSample(item)},
            firstImage(item) ? h(PublicObservation, {src: firstImage(item), compact: true, label:'题目 '+(item.row_index+1),t}) :
              h('div',{className:'bfw-question-placeholder'},'文字题'),
            h('span', {className: 'bfw-muted'},'题目 '+(item.row_index+1)), h('p', {className: 'bfw-question-preview'}, item.question_presentation?.stem || questionText(item.question)),
            h('span', {className: 'bfw-muted'}, templateName(item.template_id) + ' · ' + (item.split || ''))))),
      h('div', {className: 'bfw-pagination'},
        button(t('previous'), () => update({page: route.page - 1}), {disabled: route.page <= 1 || dataset.loading}),
        h('span', null, t('page') + ' ' + route.page + ' / ' + Math.max(1, data?.pages || 1)),
        button(t('nextPage'), () => update({page: route.page + 1}), {disabled: !data || route.page >= data.pages || dataset.loading})),
      route.stage==='S4' && currentSample && h(QuestionDialog,{key:currentSample.row_index,item:currentSample,close:()=>setSample(null),t}));
      const planReceipt = plan ? {id: conversationPlan || !selectedRunId
        ? summary.value?.plan_source?.operation_id || 'saved-plan' : selectedRunId,
        state: 'completed', label: t('persistedPlan')} : null;
      const separatePlan = conversationPlan && JSON.stringify(conversationPlan) !== JSON.stringify(run.value?.plan);
      const plannedItems = plan?.selections?.length ? plan.selections.reduce((total, row) => total + row.count, 0) : plan?.inputs?.target_items;
      const filesError = sceneArtifacts.error || run.error;
      const productFiles = stageFiles.filter((row) => (row.url || row.data) && !(separatePlan &&
        (row.title === '评测计划' && row.stage === 'S1' || row.title === '评测配置' && row.stage === 'S4')));
      const navigationState = (id) => stageState(id, navigationPipeline(id), progressSceneForStage(id, navigationScene(id), sceneJobs), progress.value?.operations,
        planReceipt, navigationScene(id)?.run_id === artifactScene ? sceneArtifacts.value?.items || [] :
          navigationPipeline(id)?.id === selectedRunId ? allStageFiles : [],
        id === 'S2' && (collectionPlan?.assessment?.scenes || collectionPlan?.inputs?.target_scene_count || progress.value?.current_collection)
          ? sceneCollectionProgress(collectionPlan, sceneJobs, progress.value?.current_collection) : null, progress.value?.generation);
      const progressStates = Object.fromEntries(STAGES.map((row) => [row.id, navigationState(row.id)]));
      const progressRunning = conversationRunning || (progress.value?.parallel_tasks || []).some((row) => lifecycle(row.state) === 'running') ||
        currentGeneration(progress.value?.generation)?.state === 'running';
      const liveProgressStage = executionStage(stage, progressStates, conversationRunning, progress.value?.parallel_tasks || [], progress.value?.generation);
      const liveProgressPipeline = liveProgressStage.id === 'execution' ? null : navigationPipeline(liveProgressStage.id);
      const liveProgressScene = liveProgressStage.id === 'execution' ? null : navigationScene(liveProgressStage.id);
      const finalReport = productFiles.find((row) => row.file === 'release/collection_report.json') ||
        productFiles.find((row) => !row.file && row.stage === 'S5' && row.step_id);
      const designMatch=row=>plan?.assessment?.scenes?.designs?.some(design=>sceneName(row.name).startsWith(design.name));
      const planScenes=[...(plan?.assessment?.scenes?.allow_new_scenes ? (sceneLibrary.value?.items || [])
        .filter(row=>row.origin_session===sessionId && row.available && designMatch(row)).map(row=>({...row,planCurrent:true})) : []),
        ...(planSceneLibrary.value?.items || []).filter(row=>plan?.assessment?.scenes?.selected_scenes?.some(selected=>(selected.task_id || selected.id)===row.id && selected.revision===row.revision))]
        .sort((a,b)=>Number(b.planCurrent || false)-Number(a.planCurrent || false) || Number(designMatch(b))-Number(designMatch(a)));
      const uniqueScenes=planScenes.filter((row,index)=>planScenes.findIndex(other=>sceneName(other.name).split(' · ')[0]===sceneName(row.name).split(' · ')[0])===index);
      let product;
      if (stage.id === 'S1') product = h(React.Fragment, null,
        h(PlanProduct, {plan, source: conversationPlan || !selectedRunId ? summary.value?.plan_source : run.value?.plan_source,
          artifacts: productFiles, preview: setPreview, request: prepare, save:savePlan,applyEdit:applyPlanEdit,
          scenes:uniqueScenes,t}),
        h(TemplateCatalog, {catalog, catalogQuery, setCatalogQuery, catalogPage, setCatalogPage,
          capability, setCapability, retry: () => setRevision((value) => value + 1), t}),
        runHistory);
      else if (stage.id === 'S2' && (sceneLibraryView.value || sceneLibraryView.error || sceneLibraryView.loading)) product = h(SceneLibrary, {record: sceneLibraryView,
        selection: currentSceneSelection, saving: sceneSaving || sending, error: sceneError, choose: chooseScenes,
        query: sceneQuery, search: (query) => {setSceneQuery(query); setScenePage(1);}, origin: sceneOrigin,
        filter: (origin) => {setSceneOrigin(origin); setScenePage(1);}, page: setScenePage,
        refresh: () => setRevision((value) => value + 1), preview: setPreview, t});
      else if (['S2', 'S3'].includes(stage.id)) product = h(React.Fragment, null,
        stage.id === 'S3' && h('small', {className: 'bfw-hint'}, t('annotationNote')),
        filesError && h('p', {className: 'bfw-error', role: 'alert'}, t('artifactReadFailed') + ' ' + filesError),
        (sceneArtifacts.loading || run.loading) && empty(t('loading')),
        (productFiles.length > 0 || !(sceneArtifacts.loading || run.loading || filesError)) &&
          h(stage.id === 'S2' ? SceneGallery : ArtifactGallery, {items: productFiles, preview: setPreview, t}));
      else if (stage.id === 'S4') product = h('div', {className: 'bfw-evaluation-grid'},
        h('section', {className: 'bfw-evaluation-config'}, h('h3', null, t('evaluationConfig')),
          select(t('chooseDataset'), route.dataset, changeDataset, versionOptions),
          h('dl', {className: 'bfw-definition'},
            h('div', null, h('dt', null, t('version')), h('dd', null, selectedDataset ? '数据集 '+(datasets.indexOf(selectedDataset)+1) : '—')),
            h('div', null, h('dt', null, t('targetItems')), h('dd', null, dataset.value?.report?.requested ?? plannedItems ?? '—')),
            h('div', null, h('dt', null, t('sourceType')), h('dd', null,dataset.value?.report?.source ? '能力图谱模板' : '—'))),
          plan?.selections?.length > 0 && h('details',null,h('summary',null,'题型与题量分配 · '+plan.selections.length+' 项'),
            table([t('selectedTemplates'), t('selectionTotal')],plan.selections.map((row) => [row.template_ids.map(templateName).join('、'), row.count]))),
          productFiles.length > 0 && h(ArtifactGallery, {items: productFiles, selected: [], preview: setPreview, t}),
          datasetDirectory),
        h('section', {className: 'bfw-evaluation-questions'}, h('h3', null, t('questionTable')),
          route.dataset ? datasetBrowser : empty(t('chooseDataset'))));
      else product = h(React.Fragment, null,
        select(t('chooseDataset'), route.dataset, changeDataset, versionOptions),
        productResource.status === 'failed' && !productResource.value && h('p', {className: 'bfw-error', role: 'alert'},
          t('artifactReadFailed') + ' ' + (productResource.failure?.message || '')),
        unavailableProductCount > 0 && h('p', {className: 'bfw-muted'}, t('unavailableProducts', {count: unavailableProductCount})),
        dataset.error && h('p', {className: 'bfw-error'}, dataset.error),
        !finalReport && (dataset.value?.report || selectedDataset?.report) && h(Quality, {report: dataset.value?.report || selectedDataset?.report,templateName,t}),
        !finalReport && (sceneArtifacts.loading || run.loading) ? empty(t('loading')) :
          !finalReport && filesError ? h('p', {className: 'bfw-error'}, t('artifactReadFailed') + ' ' + filesError) :
            h(ReportProduct, {item: finalReport, observedAt: sourceScene ? sceneArtifacts.updatedAt : run.updatedAt, t}),
        productFiles.length > 0 && h('details', null, h('summary', null, t('artifactFiles')),
          h(ArtifactGallery, {items: productFiles, selected: [], preview: setPreview, t})),
        unassignedProductItems.length > 0 && h('details', null, h('summary', null, t('artifactFiles')),
          h(ArtifactGallery, {items: unassignedProductItems, selected: [], preview: setPreview, t})));
    const body = h(React.Fragment, null,

        h('section', {className: 'bfw-product', 'data-stage-product': stage.id, 'aria-label': t('keyProduct') + ' · ' + t(stage.key)}, product),
        !sourceScene && run.value && h('details', {'data-run-details': true}, h('summary', null, t('runDetails')),
          h(RunDetails, {record: run.value, t})),
        h('div', {className: 'bfw-stage-footer'},
          button(t('requestChange'), prepare, {className: 'bfw-button bfw-action', 'data-request-stage': stage.id}), button(t('previousStep'), () => update({stage: STAGES[STAGES.indexOf(stage) - 1].id}), {disabled: stage.id === 'S1'}),
          button(t('nextStep'), () => update({stage: STAGES[STAGES.indexOf(stage) + 1].id}), {disabled: stage.id === 'S5'})));
    return h('div', {ref: panelRef, className: 'bfw-root', 'data-benchforge-workbench': 'true', 'data-benchforge-panel': 'true'}, h('style', null, styles), h('div', {className: 'bfw-shell'},
      h('header', {className: 'bfw-header'}, h('div', null,
        h('div', {className: 'bfw-kicker'}, 'BenchForge'), h('h1', {className: 'bfw-title'}, t('panel'))), button(t('refresh'), () => setRevision((value) => value + 1), {disabled: summary.loading})),
      h('nav', {className: 'bfw-flow', 'aria-label': t('navigation')}, STAGES.map((step) => h('button', {
        className: 'bfw-stage', type: 'button', key: step.id, 'data-stage': step.id,
        'aria-current': stage.id === step.id ? 'step' : undefined,
        onClick: () => {update({stage: step.id}); setNotice('');}},
        h('span', {className: 'bfw-stage-index', 'aria-hidden': true}, step.id.slice(1).padStart(2, '0')),
        h('span', {className: 'bfw-stage-copy'}, h('strong', {title: t(step.key)}, t(step.key + 'Nav')),
        h('span', {className: 'bfw-stage-state', 'data-stage-state': navigationState(step.id)},
          t(navigationState(step.id) === 'completed' ? 'stageDone' : navigationState(step.id))))))),
      h(StageProgress, {stage: liveProgressStage, pipeline: liveProgressPipeline, scene: liveProgressScene, record: progress,
        planReceipt, collectionPlan, artifacts: allStageFiles, conversationRunning,
        completedStages: Object.values(progressStates).filter((state) => state === 'completed').length, t}),
      h(StageReviewPanel,{value:reviewRecord.value,t,change:() => prepare(''),act:async (action) => {
        try {await reviewAction(action); if (action === 'pause' && conversationRunning) await stopExecution(); if (action === 'continue' && !conversationRunning) {
          await submitWithScenes({text:'用户已点击立即继续。读取阶段等待状态并复用当前计划和产物继续生产。',inserted:false,reviewContinued:true},setDemandError);
        }} catch(error) {setNotice(error.message);}
      }}),
      demandError && h('p', {className: 'bfw-error', role: 'alert'}, demandError),
      notice && h('div', {className: 'bfw-notice', role: 'status'}, notice),
      [summary.error, run.error, dataset.error].filter(Boolean).map((error, i) => h('div', {className: 'bfw-error', role: 'alert', key: i}, error)),
      h('section', {id: 'bfw-content-' + sessionId, 'aria-label': t(stage.key)}, body),
      preview && h(PreviewDialog, {key: preview.url || preview.title, item: preview, close: () => setPreview(null), t})));
  }
  return {Workbench};
}
