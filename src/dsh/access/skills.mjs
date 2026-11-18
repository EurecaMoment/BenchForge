import * as FileSkills from '@deepseek-ai/dsh-skill-filesystem';

export const name = 'benchforge-component-skills';
export const inject = ['skills', 'benchforgeAccess'];

/** Preset-scoped native provider reads the Host's published documentation copy. */
export async function apply(ctx) {
  await ctx.plugin(FileSkills, {providerName: 'benchforge-components', includeDefaultRoots: false,
    customSkillDirs: [ctx.benchforgeAccess.skillsRoot], watch: false});
}
