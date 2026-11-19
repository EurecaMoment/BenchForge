import * as FileSkills from '@deepseek-ai/dsh-skill-filesystem';

export const name = 'benchforge-component-skills';
export const inject = ['skills', 'benchforgeAccess'];

/** Preset-scoped native provider reads the Host's published documentation copy. */
/** 仅从宿主发布的受管技能目录注册 BenchForge 技能，避免默认目录引入额外来源。 */
export async function apply(ctx) {
  await ctx.plugin(FileSkills, {providerName: 'benchforge-components', includeDefaultRoots: false,
    customSkillDirs: [ctx.benchforgeAccess.skillsRoot], watch: false});
}
