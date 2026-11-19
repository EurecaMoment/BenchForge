import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {loadServiceConfig} from './service_client.mjs';

const cases = JSON.parse(await fs.readFile(new URL('../../../../tests/fixtures/service_config.json', import.meta.url), 'utf8'));

for (const fixture of cases) {
  /** 共用服务配置样例，核对 DSH 与两个 Python 入口的公开解析行为。 */
  test(`shared service configuration: ${fixture.name}`, async () => {
    /** 在临时目录构造跨语言共用配置，核对地址、凭据和路径而不访问部署环境。 */
    const root = await fs.mkdtemp(path.join(os.tmpdir(), 'benchforge-config-'));
    try {
      for (const [name, document] of Object.entries(fixture.files)) {
        const file = path.join(root, name);
        await fs.mkdir(path.dirname(file), {recursive: true});
        await fs.writeFile(file, JSON.stringify(document));
      }
      /** 只读取测试样例声明的环境，避免宿主凭据影响结果。 */
      const getEnvironmentValue = (name) => fixture.environment[name];
      /** 用当前样例读取配置，允许只读调用省略操作凭据。 */
      const load = () => loadServiceConfig(path.join(root, 'client.json'), {requireOperatorToken: false, getEnvironmentValue});
      if (fixture.error) {
        await assert.rejects(load, new RegExp(fixture.error));
        return;
      }
      const result = (await load()).config;
      for (const [key, value] of Object.entries(fixture.expected)) assert.equal(result[key], value);
      for (const [key, relative] of Object.entries(fixture.paths || {})) assert.equal(result[key], path.join(root, relative));
    } finally {
      await fs.rm(root, {recursive: true, force: true});
    }
  });
}
