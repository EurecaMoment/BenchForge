import assert from 'node:assert/strict';
import test from 'node:test';
import {Context} from '@deepseek-ai/cordis';
import * as operations from './session_operations.mjs';

test('production host activates in Cordis and releases its domain on unload', async () => {
  const ctx = new Context();
  const records = new Map();
  let registered, opened = 0, closed = 0;
  ctx.provide('sessionProjections', {register: async definition => {
    registered = definition;
    return {stateOf: session => definition.init(session.header)};
  }});
  ctx.provide('storageDomain', {open: async () => {
    opened++;
    return {table: () => records, close: async () => {closed++;}};
  }});
  ctx.provide('agents', {list: () => [], get: () => undefined});
  ctx.provide('sessionController', {resolveAgent: async () => ({agent: undefined})});

  const fiber = ctx.plugin(operations, {});
  try {
    await fiber;
    const service = ctx.get('benchforgeSessions');
    assert.ok(service?.production, 'production host must activate with the built-in Cordis logger');
    assert.equal(typeof service.execution.dispose, 'function');
    assert.equal(registered.key, 'benchforgeProduction');
    assert.equal(opened, 1);
  } finally {
    await fiber.dispose();
  }
  assert.equal(closed, 1);
  assert.equal(ctx.get('benchforgeSessions'), undefined);
});
