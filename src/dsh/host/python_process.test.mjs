import assert from 'node:assert/strict';
import test from 'node:test';
import {executePython, createPythonProcessScope} from './python_process.mjs';

test('JSON process boundary forces UTF-8 while retaining caller environment', async () => {
  const {stdout} = await executePython(process.execPath, ['-e',
    'process.stdout.write(JSON.stringify([process.env.PYTHONIOENCODING,process.env.PYTHONUTF8,process.env.BENCHFORGE_TEST_VALUE]))'],
  {env: {PYTHONIOENCODING:'gbk', BENCHFORGE_TEST_VALUE:'中文'}});
  assert.deepEqual(JSON.parse(stdout), ['utf-8','1','中文']);
});

test('Host disposal terminates in-flight processes and rejects queued launches', async () => {
  const scope = createPythonProcessScope();
  const result = scope.execute(process.execPath, ['-e', 'setInterval(() => {}, 1000)']);
  const rejected = assert.rejects(result, {name: 'AbortError'});
  await scope.dispose();
  await rejected;
  assert.ok(result.child.exitCode !== null || result.child.signalCode !== null);
  assert.throws(() => scope.execute(process.execPath, ['-e', '']), {name: 'AbortError'});
});
