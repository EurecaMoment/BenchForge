import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {standardDecoratorPlugin, vitestExecArgv} from '../../third_party/deepseek-harness4BenchForge/vitest.shared.ts';

const repository = fileURLToPath(new URL('../..', import.meta.url));
const native = path.join(repository, 'third_party/deepseek-harness4BenchForge');
const require = createRequire(path.join(native, 'package.json'));
const {defineConfig} = await import(pathToFileURL(require.resolve('vitest/config')).href);
const ts = require('typescript');
const config = ts.parseConfigFileTextToJson('tsconfig.base.json', readFileSync(path.join(native, 'tsconfig.base.json'), 'utf8'));
if (config.error) throw new Error('Cannot read the native DSH source aliases');
const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
// Explicit source aliases also apply to BenchForge .mjs outside DSH's tree.
// Loading old lib/ in that half of the graph would duplicate Cordis identities.
const alias = Object.entries(config.config.compilerOptions.paths)
  .sort(([a], [b]) => b.length - a.length)
  .map(([name, values]) => ({find: new RegExp('^' + name.split('*').map(escape).join('(.*)') + '$'),
    replacement: path.resolve(native, values[0]).replaceAll('\\', '/').replace('*', '$1')}));
alias.push({find: /^vitest$/, replacement: path.join(path.dirname(require.resolve('vitest/package.json')), 'dist/index.js')});
alias.push({find: /^zod$/, replacement: require.resolve('zod')});

export default defineConfig({
  root: repository, plugins: [standardDecoratorPlugin()], resolve: {alias},
  test: {include: ['tests/dsh_host_integration/*.spec.ts'], environment: 'node',
    pool: 'forks', execArgv: vitestExecArgv, maxWorkers: 1, fileParallelism: false,
    testTimeout: 45000, hookTimeout: 45000, restoreMocks: true},
});
