import {loadServiceConfig} from '../tools/scene/service_client.mjs';

/** Forward only the configured database credential through DSH's Host seam. */
export async function databaseEnvironment(configFile, snapshot, overrides = {}) {
  const get = name => overrides[name] ?? snapshot.get(name)?.value;
  if (!configFile) return {};
  const {config} = await loadServiceConfig(configFile, {
    requireOperatorToken: false, getEnvironmentValue: get,
  });
  const name = config.database_url_env || (!config.database_url && 'BENCHFORGE_DATABASE_URL');
  if (!name) return {};
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) throw new Error('Invalid database credential variable');
  const value = get(name);
  if (value === undefined && config.database_url_env)
    throw new Error(`database_url_env references unset environment variable ${name}`);
  return value === undefined ? {} : {[name]: value};
}
