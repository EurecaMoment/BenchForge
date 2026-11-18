import css from './composer.module.css';
import {PRODUCTION_SOURCE} from '../production-input.js';

export function createComposerComponents({React, ui}) {
  const h = React.createElement;
  const {Button, Input, Switch} = ui;

  function ProductionComposer({t, sessionId, useInput, inputActions, useStore, actions, hero,
    canSubmit, configureProduction, openScenes}) {
    const demand = useStore((value) => value.demand);
    const input = useInput((value) => value);
    const enabled = input.occurrences.some((row) => row.source === PRODUCTION_SOURCE);
    const busy = input.phase !== 'plain' || Boolean(input.claim);
    const [error, setError] = React.useState('');
    const [advanced, setAdvanced] = React.useState(false);
    const advancedId = React.useId();
    const toggle = (checked) => {
      try {configureProduction(demand, checked); setError('');}
      catch (reason) {setError(reason.message);}
    };
    const change = (patch) => {
      const next = {...demand, ...patch};
      try {
        configureProduction(next, true);
        actions.changeDemand(patch); setError('');
      } catch (reason) {setError(reason.message);}
    };
    const restore = () => {
      const span = inputActions.captureInsertion();
      if (!inputActions.insertText(demand.objective + '\n', {...span, end: span.start})) {setError(t('draftBusy')); return;}
      actions.changeDemand({objective: ''});
    };
    const field = (key, label, attributes = {}, className = css.field) => h('label', {className}, h('span', null, t(label)),
      h(Input, {...attributes, value: demand[key], onChange: (event) => change({[key]: event.target.value})}));
    const select = (key, label, options) => h('select', {
      className: css.select, 'aria-label': t(label), title: t(label), value: demand[key],
      onChange: (event) => change({[key]: event.target.value}),
    }, options.map(([value, title]) => h('option', {key: value, value}, t(title))));
    const append = demand.mode === 'append';
    const errorMessage = error || (enabled && !canSubmit ? t('permissionsPending') : '');
    return h('section', {className: css.root + (hero ? ' ' + css.hero : ''), 'data-benchforge-composer': sessionId},
      h('div', {className: css.toolbar},
        h('label', {className: css.toggleRow},
          h(Switch, {checked: enabled, label: t('enableProductionSettings'), title: t('composerProductionHint'), disabled: busy, onChange: toggle}),
          h('span', {className: css.toggleLabel}, t('productionToolbar'))),
        enabled && h('fieldset', {className: css.controls, disabled: busy, 'data-production-options': true},
          h('legend', {className: css.srOnly}, t('productionSettings')),
          select('mode', 'task', [['new', 'newProductionShort'], ['append', 'appendRequest']]),
          !append && field('count', 'itemCountShort', {type: 'number', min: 1, step: 1,
            'aria-label': t('targetItems'), 'data-production-count': true}, css.count),
          !append && select('executionMode', 'executionMode', [['production', 'productionMode'], ['exploration', 'explorationMode']]),
          !append && select('quantityContract', 'quantityContract', [['exact', 'exactQuantityShort'], ['minimum', 'minimumQuantityShort'], ['best_effort', 'bestEffortQuantity']]),
          h(Button, {size: 'sm', variant: 'ghost', onClick: openScenes}, t('sceneLibrary')),
          !append && h(Button, {size: 'sm', variant: 'ghost', 'aria-expanded': advanced,
            'aria-controls': advancedId, onClick: () => setAdvanced(!advanced)}, t('moreProductionSettings')))),
      enabled && !append && advanced && h('fieldset', {id: advancedId, className: css.advanced,
        disabled: busy, 'data-production-advanced': true},
        h('legend', {className: css.srOnly}, t('moreProductionSettings')),
        field('capability', 'capabilityRequest', {placeholder: t('capabilityPlaceholder')}, css.capability),
        field('sceneCount', 'sceneCount', {type: 'number', min: 1, step: 1, placeholder: t('optionalAuto')}),
        field('storageBudget', 'storageBudget', {type: 'number', min: 0, step: 'any', placeholder: t('optionalAuto')})),
      enabled && demand.objective?.trim() && h(Button, {size: 'sm', variant: 'outline', disabled: busy,
        'data-restore-production-draft': true, onClick: restore}, t('restoreProductionDraft')),
      errorMessage && h('div', {'aria-live': 'polite'},
        h('p', {className: css.error, role: 'alert'}, errorMessage)));
  }
  return {ProductionComposer};
}
