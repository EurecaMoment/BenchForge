import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {databaseEnvironment} from './database_environment.mjs';

test('only the selected database credential crosses the Host subprocess seam', /** 验证宿主进程、取消信号和资源清理在该用例中的具体边界，完成准备、调用和结果断言。用例：only the selected database credential crosses the Host subprocess seam。 */ async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'bf-database-env-'));
  t.after(() => fs.rm(root, {recursive: true, force: true}));
  const config = path.join(root, 'client.json');
  await fs.writeFile(config, JSON.stringify({execution_mode: 'exploration', database_url_env: 'PG_SECRET'}));
  const snapshot = new Map([['PG_SECRET', {value: 'postgresql://local/test'}], ['OTHER_TOKEN', {value: 'hidden'}]]);
  assert.deepEqual(await databaseEnvironment(config, snapshot), {PG_SECRET: 'postgresql://local/test'});
  await assert.rejects(databaseEnvironment(config, new Map()), /unset environment variable PG_SECRET/);
});
