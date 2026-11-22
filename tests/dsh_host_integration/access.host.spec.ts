/** Keyless Host integration: real controllers, AgentLoop, filesystem and bwrap. */
import {mkdir, mkdtemp, readFile, realpath, rm, symlink, writeFile} from 'node:fs/promises'
import {homedir} from 'node:os'
import {join} from 'node:path'
import {randomUUID} from 'node:crypto'
import {fileURLToPath} from 'node:url'
import {Context} from '@deepseek-ai/cordis'
import Loader from '@deepseek-ai/cordis-plugin-loader'
import Group from '@deepseek-ai/cordis-plugin-group'
import {mountAgentLoopTestDependencies, mountAgentLoopTestHarness} from '@deepseek-ai/dsh-agent-loop-testkit'
import AgentPresets from '@deepseek-ai/dsh-agent-preset-registry'
import Storage from '@deepseek-ai/dsh-storage'
import * as StorageJson from '@deepseek-ai/dsh-storage-json'
import * as StorageDomain from '@deepseek-ai/dsh-storage-domain'
import * as SessionPersistence from '@deepseek-ai/dsh-session-persistence-jsonl'
import WorkspaceRegistry from '@deepseek-ai/dsh-workspace'
import SandboxPolicy from '@deepseek-ai/dsh-sandbox-policy'
import SandboxedFileSystem from '@deepseek-ai/dsh-fs-sandbox'
import WorkingDirectory from '@deepseek-ai/dsh-working-directory'
import Skills from '@deepseek-ai/dsh-skill'
import * as ToolSkill from '@deepseek-ai/dsh-tool-skill'
import * as ToolFs from '@deepseek-ai/dsh-tool-fs'
import * as ToolBash from '@deepseek-ai/dsh-tool-bash'
import * as ShellEnv from '@deepseek-ai/dsh-shell-env'
import LocalSubprocess from '@deepseek-ai/dsh-subprocess-local'
import {LocalSandboxProvider} from '@deepseek-ai/dsh-sandbox-local'
import {SandboxBashExecutor} from '@deepseek-ai/dsh-bash-sandbox'
import {defineTool} from '@deepseek-ai/dsh-tools'
import {describe, expect, it, onTestFinished} from 'vitest'
import {createSessionTestController} from '../../third_party/deepseek-harness4BenchForge/packages/api/session-controller/tests/test-remote.ts'
import * as AccessHost from '../../src/dsh/access/host.mjs'
import * as ComponentSkills from '../../src/dsh/access/skills.mjs'
import {currentAccessScope} from '../../src/dsh/access/context.mjs'

/** 将测试命令参数按 POSIX 单引号规则转义，保证路径包含特殊字符时仍能安全传递。 */
const quote = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'"
/** 合并原生工具返回的文本块，供真实宿主集成测试断言。 */
const text = (result: any) => result.content.filter((part: any) => part.type === 'text').map((part: any) => part.text).join('\n')

/** 创建带真实原生服务的访问宿主测试环境，登记测试结束后的工作区及上下文清理。 */
async function boot(existingRoot?: string) {
  const root = existingRoot || await realpath(await mkdtemp(join(process.env.BENCHFORGE_HOST_TEST_ROOT || homedir(), '.benchforge-host-test-')))
  const ctx = new Context()
  onTestFinished(async () => {
    try {await ctx.fiber.dispose()} finally {await rm(root, {recursive: true, force: true})}
  })
  ctx.baseUrl = new URL('../../third_party/deepseek-harness4BenchForge/', import.meta.url).href
  await ctx.plugin(Loader)
  ctx.loader.builtins.group = Group
  await mountAgentLoopTestDependencies(ctx)
  await ctx.plugin(Storage)
  await ctx.plugin(StorageJson, {root: join(root, 'host-storage')})
  await ctx.plugin(StorageDomain, {backend: 'json'})
  await ctx.plugin(SessionPersistence, {root: join(root, 'sessions')})
  await ctx.plugin(WorkspaceRegistry)
  await ctx.plugin(SandboxPolicy, {mode: 'workspace-write', workspaceRoot: root})
  await ctx.plugin(SandboxedFileSystem, {cwd: root})
  await ctx.plugin(WorkingDirectory)
  await ctx.plugin(AccessHost, {workspaceRoot: join(root, 'workspaces'), resourceRoot: join(root, 'resources'),
    skillsSource: fileURLToPath(new URL('../../src/dsh/skills/', import.meta.url))})
  await ctx.plugin(Skills)
  await ctx.plugin(ComponentSkills)
  await ctx.plugin(ToolSkill)
  await ctx.plugin(ToolFs, {readImage: false})
  await ctx.plugin(LocalSubprocess)
  await ctx.plugin(LocalSandboxProvider)
  await ctx.plugin(SandboxBashExecutor, {timeoutMs: 10000, graceMs: 100, cwd: root})
  await ctx.plugin(ShellEnv, {dshHome: join(root, 'host-home')})
  await ctx.plugin(ToolBash, {enableRunInBackground: false})
  await mountAgentLoopTestHarness(ctx)
  await ctx.plugin(AgentPresets, {default: 'benchforge'})
  const unregisterPreset = await ctx.agentPresets.register({id: 'benchforge', plugins: []})
  ctx.effect(() => unregisterPreset)
  ctx.provide('typertGateway', {/** 为无外部网关的集成夹具返回空远程事件队列。 */ getPendingRemoteEvents: () => []} as never)
  const controller = await createSessionTestController(ctx, {
    cwd: root, /** 返回不会实际调用模型的测试选择，保持会话创建契约完整。 */ defaultModelSelection: () => ({provider: 'unused', model: 'no-model-call'}),
  })
  /** 通过官方控制器创建 BenchForge 会话，并确认已发布对应 Agent。 */
  const create = async () => {
    const result = await controller.create({agentPreset: 'benchforge'})
    const agent = ctx.agents.get(result.sessionId)
    if (!agent) throw new Error('Native controller did not publish its Agent')
    return agent
  }
  /** 以原生工具批次执行测试调用，并取出单个实际工具结果。 */
  const call = async (agent: any, name: string, args: object) => {
    const batch = await ctx.tools.execute({agent, signal: new AbortController().signal,
      calls: [{type: 'tool-call', id: randomUUID() as never, name, arguments: JSON.stringify(args)}]})
    return batch.results[0]!.result
  }
  return {ctx, root, controller, create, call}
}

describe.skipIf(process.platform !== 'linux')('BenchForge with native DSH Host services', /** 验证BenchForge 工具与宿主集成在该用例中的具体边界，完成准备、调用和结果断言。用例：BenchForge with native DSH Host services。 */ () => {
  it('uses the selected project for both sidebar membership and production files', async () => {
    const {ctx, root, controller, call} = await boot()
    const project = join(root, 'production-project')
    await mkdir(project)
    const workspace = await ctx.workspaceRegistry.create(project, 'Production')
    const createInWorkspace = async () => {
      const {sessionId} = await controller.create({sessionId: randomUUID() as never, agentPreset: 'benchforge', workspaceId: workspace.id})
      const agent = ctx.agents.get(sessionId)
      if (!agent) throw new Error('Selected-workspace session was not published')
      return agent
    }
    const first = await createInWorkspace()
    const second = await createInWorkspace()
    expect(workspace.sessionIds).toEqual(expect.arrayContaining([first.id, second.id]))
    const firstRoot = ctx.benchforgeAccess.scope(first.session).root_path
    const secondRoot = ctx.benchforgeAccess.scope(second.session).root_path
    expect(firstRoot).toBe(project)
    expect(secondRoot).toBe(project)
    expect(first.session.header.cwd).toBe(project)
    expect(ctx.benchforgeAccess.scope(first.session).workspace_id).toBe(workspace.id)
    const secret = join(root, 'outside.txt')
    await writeFile(secret, 'OUTSIDE_PROJECT')
    const read = await call(first, 'read', {file_path: secret})
    expect(read.isError, text(read)).toBe(true)
    const result = await call(first, 'bash', {description: 'Check execution and write access inside the selected project',
      command: 'test ! -e ' + quote(secret) + ' && printf own > owned.txt'})
    expect(result.isError, text(result)).toBe(false)
    const shared = await call(second, 'read', {file_path: 'owned.txt'})
    expect(shared.isError, text(shared)).toBe(false)
    expect(text(shared)).toContain('own')
  })

  it('creates isolated native sessions and enforces files, bash, skills and trusted tool scope', /** 验证BenchForge 工具与宿主集成在该用例中的具体边界，完成准备、调用和结果断言。用例：creates isolated native sessions and enforces files, bash, skills and trusted tool scope。 */ async () => {
    const {ctx, root, controller, create, call} = await boot()
    const [a, b] = await Promise.all([create(), create()])
    const aRoot = a.session.header.cwd!, bRoot = b.session.header.cwd!
    expect(aRoot).not.toBe(root)
    expect(aRoot).not.toBe(bRoot)
    expect(a.session.header.agentPreset).toBe('benchforge')
    expect(ctx.workspaceRegistry.list().some(row => row.path === aRoot || row.path === bRoot)).toBe(false)
    const artifactRoots = ctx.benchforgeAccess.rootsFor([a.id]).map((entry: {path: string}) => entry.path)
    expect(artifactRoots).toContain(aRoot)
    expect(artifactRoots).not.toContain(bRoot)
    expect(ctx.benchforgeAccess.scope(a.session).session_id).toBe(a.id)
    expect((await controller.resolveAgent(a.id)).agent).toBe(a)
    const observation = await ctx.sessionQuery.observeSession(a.id)
    try {expect(observation.header.cwd).toBe(aRoot)} finally {observation[Symbol.dispose]()}
    const secret = join(bRoot, 'tasks', 'secret.txt')
    await writeFile(secret, 'OTHER_SESSION_PRIVATE')
    await symlink(bRoot, join(aRoot, 'escape'))

    const written = await call(a, 'write', {file_path: 'tasks/owned.txt', content: 'OWNED'})
    expect(written.isError, text(written)).toBe(false)
    expect(await readFile(join(aRoot, 'tasks', 'owned.txt'), 'utf8')).toBe('OWNED')
    const ownRead = await call(a, 'read', {file_path: 'tasks/owned.txt'})
    expect(ownRead.isError, text(ownRead)).toBe(false)
    expect(text(ownRead)).toContain('OWNED')
    for (const file of [secret, join(aRoot, 'escape', 'tasks', 'secret.txt')]) {
      const denied = await call(a, 'read', {file_path: file})
      expect(denied.isError, text(denied)).toBe(true)
      expect(text(denied)).not.toContain('OTHER_SESSION_PRIVATE')
    }
    await expect(ctx.fs.resolve(secret, {sandboxPolicy: ctx.sandboxPolicy.resolve({session: a.session})}))
      .rejects.toMatchObject({code: 'FS_SANDBOX_DENIED'})

    const catalog = await ctx.skills.list({cwd: aRoot, scope: a})
    expect(catalog.map(row => row.name).sort()).toEqual([
      'dspark', 'fast-da3', 'fast-flux', 'fast-qwen', 'fast-sam3', 'fast-trellis', 'isaac-sim',
    ])
    const skill = await call(a, 'skill', {name: 'isaac-sim'})
    expect(skill.isError, text(skill)).toBe(false)
    expect(text(skill)).toContain('Isaac')

    ctx.tools.register(defineTool({name: 'access_probe', description: 'Return Host-owned identity for this test',
      parameters: {session_id: {type: 'string'}}, /** 声明测试读取工具可并发，以覆盖异步作用域隔离。 */ isConcurrencySafe: () => true,
      output: {schema: {type: 'string'}, /** 将测试工具返回值包装为原生文本块。 */ render: (_args, value) => [{type: 'text', text: value}]},
      /** 在异步边界后读取可信作用域，验证宿主上下文能够正确传递。 */
      async execute() {await Promise.resolve(); return JSON.stringify(currentAccessScope())},
    }))
    const [aScope, bScope] = await Promise.all([
      call(a, 'access_probe', {session_id: b.id}), call(b, 'access_probe', {session_id: a.id}),
    ])
    expect(JSON.parse(text(aScope)).session_id).toBe(a.id)
    expect(JSON.parse(text(bScope)).session_id).toBe(b.id)
    expect(currentAccessScope()).toBeUndefined()

    // No skip when bwrap is missing: this suite is a release acceptance gate.
    const shell = await call(a, 'bash', {description: 'Check real per-conversation process isolation',
      command: 'cat tasks/owned.txt && test ! -e ' + quote(secret) +
        ' && test ! -e escape/tasks/secret.txt && printf ok > tasks/from-bash.txt'})
    expect(shell.isError, text(shell)).toBe(false)
    expect(text(shell)).toContain('OWNED')
    expect(text(shell)).not.toContain('OTHER_SESSION_PRIVATE')
    expect(await readFile(join(aRoot, 'tasks', 'from-bash.txt'), 'utf8')).toBe('ok')
    expect(await readFile(secret, 'utf8')).toBe('OTHER_SESSION_PRIVATE')
    expect(a.status).toBe('idle')
    expect(b.status).toBe('idle')
    expect(await ctx.sessions.flush(a.session), 'Native durability checkpoint must have a persistence listener').toBe(true)
    expect(await ctx.sessions.flush(b.session)).toBe(true)
    const stored = await ctx.sessionPersistence.list()
    for (const agent of [a, b]) {
      const record = stored.find(row => row.header.id === agent.id)
      expect(record, 'Session must be in physical persistence').toBeDefined()
      expect(String(record!.revision)).not.toMatch(/^memory:/)
    }
  })

  it('flushes JSONL and resumes the same workspace from a fresh Host Context', /** 验证BenchForge 工具与宿主集成在该用例中的具体边界，完成准备、调用和结果断言。用例：flushes JSONL and resumes the same workspace from a fresh Host Context。 */ async () => {
    const first = await boot()
    const a = await first.create(), b = await first.create()
    const originalScope = first.ctx.benchforgeAccess.scope(a.session)
    // The native JSONL header contract materializes this optional live default.
    // Preserve exact comparison of every identity, path and lineage field.
    const originalHeader = {...a.session.header, delegationDepth: a.session.header.delegationDepth ?? 0}
    const file = 'tasks/persisted-draft.txt'
    const written = await first.call(a, 'write', {file_path: file, content: 'PERSISTED_DRAFT'})
    expect(written.isError, text(written)).toBe(false)
    const secret = join(b.session.header.cwd!, 'tasks', 'private.txt')
    await writeFile(secret, 'OTHER_SESSION_PRIVATE')
    expect(await first.ctx.sessions.flush(a.session)).toBe(true)
    expect(await first.ctx.sessions.flush(b.session)).toBe(true)
    const before = (await first.ctx.sessionPersistence.list()).find(row => row.header.id === a.id)
    expect(before).toBeDefined()
    expect(String(before!.revision)).not.toMatch(/^memory:/)
    await first.ctx.fiber.dispose()

    // A different Context recreates every service; it cannot observe old live maps.
    const second = await boot(first.root)
    try {
      expect(second.ctx.agents.get(a.id)).toBeUndefined()
      const durable = (await second.ctx.sessionPersistence.list()).find(row => row.header.id === a.id)
      expect(durable).toBeDefined()
      expect(String(durable!.revision)).not.toMatch(/^memory:/)
      expect(durable!.header).toEqual(originalHeader)
      const resumed = await second.controller.resolveAgent(a.id)
      if ('error' in resumed) throw resumed.error
      expect(resumed.agent).not.toBe(a)
      expect(resumed.agent.session.header).toEqual(originalHeader)
      expect(resumed.agent.session.header.cwd).toBe(originalScope.root_path)
      expect(second.ctx.benchforgeAccess.scope(resumed.agent.session)).toEqual(originalScope)
      const read = await second.call(resumed.agent, 'read', {file_path: file})
      expect(read.isError, text(read)).toBe(false)
      expect(text(read)).toContain('PERSISTED_DRAFT')
      const denied = await second.call(resumed.agent, 'read', {file_path: secret})
      expect(denied.isError, text(denied)).toBe(true)
      expect(text(denied)).not.toContain('OTHER_SESSION_PRIVATE')
      expect(await second.ctx.sessions.flush(resumed.agent.session)).toBe(true)
    } finally {await second.ctx.fiber.dispose()}
  })
})
