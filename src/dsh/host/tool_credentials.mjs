import fs from 'node:fs/promises';
import path from 'node:path';

export async function configuredProviders(configPath) {
  if (!configPath) return {};
  try { return JSON.parse(await fs.readFile(path.resolve(configPath), 'utf8')).providers || {}; }
  catch (error) { if (error.code === 'ENOENT') return {}; throw error; }
}

export function modelRole(providers, role) {
  const configured = ['DSpark4A100','FastQwen'].map(name=>providers[name]).filter(spec=>spec?.model);
  if (configured.length > 1) throw new Error('Configure one primary language model provider; use roles for overrides');
  const provider = configured[0];
  return provider ? {...provider,...provider.roles?.[role]} : undefined;
}

// Only the tool consuming this provider receives its configured credential.
export function providerEnvironment(base, snapshot, tokenName) {
  if (!tokenName) return base;
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(tokenName)) throw new Error('Invalid provider credential variable');
  const value = base[tokenName] ?? snapshot.get(tokenName)?.value;
  if (value === undefined) return base; // The provider reports a missing credential when invoked.
  return {...base, [tokenName]: value};
}
