import {cp, mkdir, lstat, readFile, stat, writeFile} from 'node:fs/promises';
import path from 'node:path';

/** Ship the actual public sample, without its heavyweight simulator replay assets. */
export async function stageExample(source, destination) {
  const manifest = JSON.parse(await readFile(path.join(source, 'bootstrap.json'), 'utf8'));
  for (const file of [manifest.conversation, ...manifest.artifacts.map(row => row.path)]) {
    if (!(await stat(path.join(source, file))).isFile()) throw new Error('Example source is incomplete: ' + file);
  }
  const metadata = /\.(json|jsonl|md|txt|zstd)$/;
  const included = relative => {
    if (relative.startsWith(manifest.workbench.dataset + '/')) return true;
    if (relative.includes('/scene_resource_')) return false;
    return metadata.test(relative) || /(?:view_\d+|reference)\.(png|jpe?g)$/.test(relative);
  };
  await mkdir(destination, {recursive: true});
  await cp(source, destination, {recursive: true, filter: async file => {
    const info = await lstat(file);
    if (info.isSymbolicLink()) throw new Error('Example cannot include symbolic links: ' + file);
    return info.isDirectory() || included(path.relative(source, file).replaceAll('\\', '/'));
  }});
  await writeFile(path.join(destination, 'PACKAGE-SCOPE.md'),
    'This package contains the original example conversation, three scene descriptions, public capture images, ' +
    'ten evidence records and the complete public 25-question sample. It does not include the heavyweight ' +
    'simulator replay texture/mesh closure or an answer library. Use the repository example for replay.\n');
}
