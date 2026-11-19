import assert from 'node:assert/strict';
import {readFile, readdir} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import test from 'node:test';
import {Context} from '@deepseek-ai/cordis';
import SkillRegistry from '@deepseek-ai/dsh-skill';
import * as SkillFileSystem from '@deepseek-ai/dsh-skill-filesystem';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../skills');

test('native DSH discovery loads packaged component skills and their references', /** 验证BenchForge 工具与宿主集成在该用例中的具体边界，完成准备、调用和结果断言。用例：native DSH discovery loads packaged component skills and their references。 */ async () => {
  const ctx = new Context();
  try {
    await ctx.plugin(SkillRegistry);
    await ctx.plugin(SkillFileSystem, {
      providerName: 'benchforge-components', includeDefaultRoots: false,
      customSkillDirs: [root], watch: false,
    });
    const directories = (await readdir(root, {withFileTypes: true}))
      .filter(entry => entry.isDirectory()).map(entry => entry.name).sort();
    const skills = await ctx.skills.list();
    assert.deepEqual(skills.map(skill => skill.name).sort(), directories);
    for (const summary of skills) {
      const skill = await ctx.skills.get(summary.name);
      assert.ok(skill?.content.length > 100, summary.name);
      assert.ok(skill.description.length > 10, summary.name);
      assert.equal(skill.invocation.modelInvocable, true);
      for (const [, reference] of skill.content.matchAll(/\]\((references\/[^)]+)\)/g)) {
        const text = await readFile(path.join(root, skill.name, reference), 'utf8');
        assert.ok(text.length > 0, reference);
      }
    }
  } finally {
    await ctx.fiber.dispose();
  }
});
