import {copyFile, mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {stageExample} from './stage_example.mjs';

const sourceRoot = new URL('..', import.meta.url);
const packageRoot = new URL('.', import.meta.url);
const files = [
  ['capabilities/benchmark_design/resources/assessment_template.json', 'dist/resources/assessment_template.json'],
  ['capabilities/task_catalog/resources/benchmark_templates.json', 'dist/resources/task_templates.json'],
  ['capabilities/benchmark_design/resources/tool-names.json', 'dist/resources/tool-names.json'],
  ['capabilities/benchmark_design/resources/production-stages.json', 'dist/resources/production-stages.json'],
  ...['README.md', 'public_measurements.py', 'public_sensor_cache.py', 'public_geometry_cache.py'].map(
    /** 为公开测量支持文件建立源码位置到发行包位置的复制清单。 */
    name => ['capabilities/data_acquisition/measurements/' + name, 'dist/task_support/' + name]),
];

for (const [source, destination] of files) {
  const target = new URL(destination, packageRoot);
  await mkdir(new URL('.', target), {recursive: true});
  await copyFile(new URL(source, sourceRoot), target);
}

await stageExample(fileURLToPath(new URL('../../example/SpatialForge/', import.meta.url)),
  fileURLToPath(new URL('dist/example/SpatialForge/', import.meta.url)));
