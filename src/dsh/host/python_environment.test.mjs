import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {executePython} from './python_process.mjs';
import {pythonHostEnvironment} from './python_environment.mjs';

test('trusted Python rejects relative module roots and removes empty search entries', /** 验证宿主进程、取消信号和资源清理在该用例中的具体边界，完成准备、调用和结果断言。用例：trusted Python rejects relative module roots and removes empty search entries。 */ () => {
  assert.throws(() => pythonHostEnvironment({PYTHONPATH: 'src'}), /absolute PYTHONPATH/);
  const root = path.resolve('trusted');
  assert.equal(pythonHostEnvironment({PYTHONPATH: path.delimiter + root + path.delimiter}).PYTHONPATH, root);
});

test('workspace modules and sitecustomize cannot run inside a credential-bearing Host query', /** 验证宿主进程、取消信号和资源清理在该用例中的具体边界，完成准备、调用和结果断言。用例：workspace modules and sitecustomize cannot run inside a credential-bearing Host query。 */ async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'benchforge-python-import-'));
  t.after(() => fs.rm(root, {recursive: true, force: true}));
  const workspace = path.join(root, 'workspace'), trusted = path.join(root, 'trusted');
  await fs.mkdir(workspace); await fs.mkdir(trusted);
  const marker = path.join(workspace, 'credential-leak');
  const attack = 'from pathlib import Path\nPath(' + JSON.stringify(marker) + ').write_text("unsafe")\n';
  await fs.writeFile(path.join(workspace, 'sitecustomize.py'), attack);
  await fs.writeFile(path.join(workspace, 'benchforge_import_probe.py'), attack);
  await fs.writeFile(path.join(trusted, 'benchforge_import_probe.py'), 'print("trusted")\n');
  const {stdout} = await executePython(process.env.BENCHFORGE_TEST_PYTHON || 'python',
    ['-m', 'benchforge_import_probe'], {cwd: workspace, env: {PYTHONPATH: trusted + path.delimiter,
      BENCHFORGE_ACCESS_KEY: 'synthetic-sensitive-value'}});
  assert.equal(stdout.trim(), 'trusted');
  await assert.rejects(fs.stat(marker), {code: 'ENOENT'});
});
