import assert from 'node:assert/strict';
import test from 'node:test';
import {Context} from '@deepseek-ai/cordis';
import * as operations from './session_operations.mjs';

test('production host activates in Cordis and releases its domain on unload', /** 验证宿主进程、取消信号和资源清理在该用例中的具体边界，完成准备、调用和结果断言。用例：production host activates in Cordis and releases its domain on unload。 */ async () => {
  const ctx = new Context();
  const records = new Map();
  let registered, opened = 0, closed = 0;
  ctx.provide('sessionProjections', {/** 记录插件注册定义，供后续测试调用或投影初始化。 */ register: async definition => {
    registered = definition;
    return {/** 根据已登记定义初始化测试会话投影。 */ stateOf: session => definition.init(session.header)};
  }});
  ctx.provide('storageDomain', {/** 提供用例配置的存储或会话打开行为，并记录资源生命周期。 */ open: async () => {
    opened++;
    return {/** 省略与当前断言无关的表格渲染。 */ table: () => records, /** 模拟资源关闭，并在用例配置了计数器时记录释放次数。 */ close: async () => {closed++;}};
  }});
  ctx.provide('agents', {/** 返回用例配置的 Agent 列表。 */ list: () => [], /** 按测试键读取内存记录或模拟服务。 */ get: () => undefined});
  ctx.provide('sessionController', {/** 返回用例配置的 Agent 解析结果。 */ resolveAgent: async () => ({agent: undefined})});
  ctx.provide('subprocess', {});

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
