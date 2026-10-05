import {join} from 'node:path';
import type {Api, Model} from '@oh-my-pi/pi-ai/types';
import {getAgentDir, getModelDbPath} from '@oh-my-pi/pi-utils/dirs';
import {Settings} from '@oh-my-pi/pi-coding-agent/config/settings';
import {ModelRegistry} from '@oh-my-pi/pi-coding-agent/config/model-registry';
import {discoverAuthStorage, loadEffectiveAuthAccountPolicyConfig} from '@oh-my-pi/pi-coding-agent/session/auth-broker-config';

// One bounded subprocess lifetime, not a resident service. Discovery can
// fail before returning ownership; the caller must terminate
// that worker rather than retry discovery in the same process.
export async function withNativeModel<T>(selector: string, consume: (route: {
 registry: ModelRegistry;
 model: Model<Api>;
 settings: Settings;
 agentDir: string;
}) => Promise<T>): Promise<T> {
 const slash = selector.indexOf('/');
 if (slash <= 0 || slash === selector.length - 1) throw new Error('explicit provider/model selector required');
 const provider = selector.slice(0, slash), id = selector.slice(slash + 1);
 const cwd = process.cwd(), agentDir = getAgentDir();
 const settings = await Settings.loadReadOnly({cwd, agentDir});
 const policy = await loadEffectiveAuthAccountPolicyConfig({settings, cwd, agentDir});
 const auth = await discoverAuthStorage(agentDir, {
  accountPolicies: policy.accountPolicies,
  authStorageOptions: {defaultReservePct: policy.defaultReservePct},
 });
 try {
  const registry = new ModelRegistry(auth, join(agentDir, 'models.yml'), {
   settings, cacheDbPath: getModelDbPath(agentDir),
  });
  await registry.hydrateCredentialScopedModelCaches();
  const model = registry.find(provider, id);
  if (!model || model.provider !== provider || model.id !== id) throw new Error(`exact model unavailable: ${selector}`);
  return await consume({registry, model, settings, agentDir});
 } finally {
  auth.close();
 }
}
