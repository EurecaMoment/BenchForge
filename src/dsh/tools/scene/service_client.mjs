import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createHmac} from 'node:crypto';
import {currentAccessScope} from '../../access/context.mjs';
import {HarnessError} from '@deepseek-ai/dsh-llm';
import {budgetRejection, captureBudgetCode} from './capture/budget.mjs';

export class ScenePreflightRejected extends HarnessError {
  /** 保留场景预检失败的完整响应与 HTTP 状态，供工具和界面展示可操作诊断。 */
  constructor(response, status) {
    super(JSON.stringify(response), 'scene_preflight_failed');
    this.status = status;
    this.response = response;
  }
}

/** 递归扫描 JSON 对象键，拒绝重复字段以免配置解析产生歧义。 */
function rejectDuplicateObjectKeys(source) {
  let index = 0;
  /** 前移解析游标，跳过 JSON 值之间的空白字符。 */
  const whitespace = () => { while (/\s/.test(source[index] || '') && index < source.length) index += 1; };
  /** 读取含转义字符的 JSON 字符串，并将其解码为对象字段名。 */
  const readString = () => {
    const start = index++;
    while (index < source.length) {
      if (source[index] === '\\') { index += 2; continue; }
      if (source[index++] === '"') return JSON.parse(source.slice(start, index));
    }
    return '';
  };
  /** 递归越过对象、数组和标量，同时核对每个对象内部的字段唯一性。 */
  const readValue = () => {
    whitespace();
    if (source[index] === '{') {
      index += 1;
      whitespace();
      const keys = new Set();
      if (source[index] === '}') { index += 1; return; }
      while (index < source.length) {
        whitespace();
        const key = readString();
        if (keys.has(key)) throw new Error(`Duplicate JSON key: ${key}`);
        keys.add(key);
        whitespace();
        index += 1;
        readValue();
        whitespace();
        if (source[index] === '}') { index += 1; return; }
        index += 1;
      }
      return;
    }
    if (source[index] === '[') {
      index += 1;
      whitespace();
      if (source[index] === ']') { index += 1; return; }
      while (index < source.length) {
        readValue();
        whitespace();
        if (source[index] === ']') { index += 1; return; }
        index += 1;
      }
      return;
    }
    if (source[index] === '"') { readString(); return; }
    while (index < source.length && !/[\s,\]}]/.test(source[index])) index += 1;
  };
  readValue();
}

/** 先拒绝重复对象键，再使用标准 JSON 解析器校验完整语法。 */
function parseStrictJson(source) {
  rejectDuplicateObjectKeys(source);
  return JSON.parse(source);
}

/** 从注入的可信环境读取器或进程环境取得变量值，兼容带元数据的返回结构。 */
async function environmentValue(getEnvironmentValue, name) {
  /** 解包环境读取结果中的值，保留普通字符串结果。 */
  const get = async key => {
    const result = getEnvironmentValue ? await getEnvironmentValue(key) : process.env[key];
    return result && typeof result === 'object' && 'value' in result ? result.value : result;
  };
  return get(name);
}

/** 展开配置字符串中的环境变量引用，缺少引用变量时立即报告错误。 */
async function expandEnvironment(value, getEnvironmentValue) {
  if (typeof value !== 'string') throw new Error('Configuration path/value must be a string');
  const reference = /\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)|%([A-Za-z_][A-Za-z0-9_]*)%/g;
  const matches = [...value.matchAll(reference)];
  let result = value;
  for (const match of matches) {
    const name = match[1] || match[2] || match[3];
    const replacement = await environmentValue(getEnvironmentValue, name);
    if (replacement === undefined || replacement === null)
      throw new Error(`Configuration references unset environment variable ${name}`);
    result = result.replace(match[0], String(replacement));
  }
  return result;
}

/** 先展开环境引用，再相对于当前配置文件解析目标路径。 */
async function resolveConfigPath(value, configPath, getEnvironmentValue) {
  const expanded = await expandEnvironment(value, getEnvironmentValue);
  const homePath = expanded === '~' ? os.homedir() : expanded.startsWith('~/') || expanded.startsWith('~\\')
    ? path.join(os.homedir(), expanded.slice(2)) : expanded;
  const resolved = path.resolve(path.dirname(configPath), homePath);
  return resolved;
}

/** 加载并严格校验服务配置及可选基础配置，解析服务地址、执行模式和操作凭据。 */
export async function loadServiceConfig(configFile, {getEnvironmentValue, requireOperatorToken = true} = {}) {
  let configPath = await resolveConfigPath(configFile, path.join(process.cwd(), '_config'), getEnvironmentValue);
  let config = parseStrictJson(await fs.readFile(configPath, 'utf8'));
  if (!config || typeof config !== 'object' || Array.isArray(config))
    throw new Error('BenchForge service configuration must be a JSON object');
  if (config.service_config) {
    const basePath = await resolveConfigPath(config.service_config, configPath, getEnvironmentValue);
    const base = parseStrictJson(await fs.readFile(basePath, 'utf8'));
    if (!base || typeof base !== 'object' || Array.isArray(base))
      throw new Error('Referenced BenchForge service configuration must be a JSON object');
    const {service_config: ignored, ...overrides} = config;
    for (const field of ['port', 'service_url', 'execution_mode', 'scene_policy_path']) {
      if (field in overrides && field in base && overrides[field] !== base[field])
        throw new Error(`${field} in ${path.basename(configPath)} conflicts with referenced service configuration`);
    }
    config = {...base, ...overrides};
    configPath = basePath;
  }

  const configuredMode = config.execution_mode || 'production';
  const environmentMode = await environmentValue(getEnvironmentValue, 'BENCHFORGE_EXECUTION_MODE');
  if (config.execution_mode !== undefined && environmentMode && environmentMode !== config.execution_mode)
    throw new Error('BENCHFORGE_EXECUTION_MODE conflicts with configured execution_mode');
  const executionMode = config.execution_mode !== undefined ? config.execution_mode : environmentMode || configuredMode;
  if (!['production', 'exploration'].includes(executionMode))
    throw new Error('execution_mode must be production or exploration');
  config.execution_mode = executionMode;

  const pathFields = [
    ['artifacts', 'BENCHFORGE_ARTIFACTS'],
    ['harness', 'BENCHFORGE_HARNESS'],
    ['models_config', 'BENCHFORGE_MODELS_CONFIG'],
    ['task_root', 'BENCHFORGE_TASK_ROOT'],
    ['scene_policy_path', 'BENCHFORGE_SCENE_POLICY'],
  ];
  for (const [field, envName] of pathFields) {
    const override = await environmentValue(getEnvironmentValue, envName);
    const value = override ?? config[field];
    if (value !== undefined && value !== null)
      config[field] = await resolveConfigPath(String(value), configPath, getEnvironmentValue);
  }

  const urlOverride = await environmentValue(getEnvironmentValue, 'BENCHFORGE_SERVICE_URL');
  const urlValue = urlOverride || config.service_url;
  if (urlValue) {
    config.service_url = (await expandEnvironment(String(urlValue), getEnvironmentValue)).replace(/\/+$/, '');
  } else {
    const port = Number(config.port ?? 3841);
    if (typeof config.port === 'boolean' || !Number.isInteger(port) || port < 1 || port > 65535)
      throw new Error('port must be between 1 and 65535');
    let host = await expandEnvironment(String(config.service_host || '127.0.0.1'), getEnvironmentValue);
    if (host.includes(':') && !host.startsWith('[')) host = `[${host}]`;
    config.service_url = `http://${host}:${port}`;
  }

  const envName = await expandEnvironment(config.operator_token_env || 'BENCHFORGE_OPERATOR_TOKEN', getEnvironmentValue);
  const configuredToken = await environmentValue(getEnvironmentValue, envName);
  const token = config.operator_token_env ? configuredToken : config.operator_token || configuredToken;
  if (requireOperatorToken && !token) throw new Error(`Configure operator_token or set ${envName}`);
  config.operator_token = token || '';

  const service = new URL(config.service_url);
  if (!['http:', 'https:'].includes(service.protocol))
    throw new Error('service_url must be an absolute HTTP or HTTPS URL');
  return {config, configPath, serviceUrl: config.service_url, operatorToken: token || '', executionMode};
}

/** 为可信会话访问作用域签名，限定服务受众并设置五分钟有效期。 */
export function signAccessScope(scope, secret, now = Date.now()) {
  const value = {
    host_id: scope.host_id, owner_id: scope.owner_id, session_id: scope.session_id,
    workspace_id: scope.workspace_id, root_path: scope.root_path,
    policy_epoch: scope.policy_epoch, root_generation: scope.root_generation,
    legacy_namespace: null, aud: 'benchforge-service', exp: Math.floor(now / 1000) + 300,
  };
  const encoded = Buffer.from(JSON.stringify(value)).toString('base64url');
  return encoded + '.' + createHmac('sha256', secret).update(encoded).digest('hex');
}

/** 创建携带操作凭据与会话作用域签名的服务请求器，并规范化及校验返回结果。 */
export function createServiceClient({serviceUrl, operatorToken}) {
  return /** 发送附有操作凭据及可信会话签名的 JSON 请求，检查服务错误并规范化返回数据。 */ async (route, args, signal, {scope = currentAccessScope()} = {}) => {
    const response = await fetch(serviceUrl + route, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', Authorization: 'Bearer ' + operatorToken,
        ...(scope ? {'X-BenchForge-Scope': signAccessScope(scope, operatorToken)} : {})},
      body: JSON.stringify(args), signal,
    });
    // Isaac camera matrices can contain -0.0; DSH's JSON transport rejects signed zero.
    const value = JSON.parse(await response.text(), (_key, item) => Object.is(item, -0) ? 0 : item);
    if (!response.ok) {
      if (response.status === 400 && value.code === 'scene_preflight_failed' && value.preflight)
        throw new ScenePreflightRejected(value, response.status);
      if (response.status === 400 && budgetRejection(value))
        throw new HarnessError(JSON.stringify(value), captureBudgetCode);
      throw Object.assign(new Error(JSON.stringify(value)), {status:response.status, response:value});
    }
    return value;
  };
}
