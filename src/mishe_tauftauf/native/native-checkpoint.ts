import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {createOpenAICodexCompatibilityMetadata} from '@oh-my-pi/pi-ai/providers/openai-codex-responses';
import type {Context, ProviderSessionState} from '@oh-my-pi/pi-ai/types';

// Research adapter for exactly these bytes, not a supported pi-ai state API.
const providerDigest = 'c35700fd2ec64987fbe06967f707a13ecff3939bc4271362d06f7bb01c1fc714';
const fields = ['sessionId','threadId','windowId','turnId','turnStartedAtUnixMs'] as const;
type Identity = {sessionId:string; threadId:string; windowId:string; turnId:string; turnStartedAtUnixMs:number};
export type Checkpoint = {schema:1; providerDigest:string; selector:string; sessionId:string; boundaryCount:number; boundaryDigest:string; identity:Identity};
type Owner = ProviderSessionState & {metadataSessions:Map<string,Identity & {turnStates:Map<string,unknown>}>};
const digest = (value:unknown) => createHash('sha256').update(JSON.stringify(value)).digest('hex');
function pin() {
 const path = Bun.resolveSync('@oh-my-pi/pi-ai/providers/openai-codex-responses', import.meta.dir);
 if (createHash('sha256').update(readFileSync(path)).digest('hex') !== providerDigest) throw new Error('UNKNOWN: checkpoint provider bytes mismatch');
}
function fail():never {throw new Error('UNKNOWN: missing or mismatched logical checkpoint');}
function exact(value:unknown, keys:readonly string[]):value is Record<string,unknown> {
 return !!value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).sort().join(',') === [...keys].sort().join(',');
}
export function isContinuation(context:Context):boolean {
 const last = context.messages.findLast(message => message.role !== 'toolResult');
 return last?.role === 'assistant';
}
export function restoreCheckpoint(state:Map<string,ProviderSessionState>, selector:string, sessionId:string, context:Context, input:unknown):void {
 pin();
 if (!exact(input,['schema','providerDigest','selector','sessionId','boundaryCount','boundaryDigest','identity'])) fail();
 if (input.schema !== 1 || input.providerDigest !== providerDigest || input.selector !== selector || input.sessionId !== sessionId || !isContinuation(context)) fail();
 if (!exact(input.identity,fields) || input.identity.sessionId !== sessionId) fail();
 for (const key of ['sessionId','threadId','windowId','turnId']) {
  const value = input.identity[key];
  if (typeof value !== 'string' || !value.trim() || value.length > 256) fail();
 }
 if (typeof input.identity.turnStartedAtUnixMs !== 'number' || !Number.isSafeInteger(input.identity.turnStartedAtUnixMs) || input.identity.turnStartedAtUnixMs < 0) fail();
 const n = input.boundaryCount;
 if (typeof n !== 'number' || !Number.isSafeInteger(n) || n < 1 || n > context.messages.length || context.messages[n-1].role !== 'assistant') fail();
 if (input.boundaryDigest !== digest({...context,messages:context.messages.slice(0,n)})) fail();
 const assistant = context.messages[n-1];
 if (assistant.role !== 'assistant') fail();
 const calls = new Map<string,string>();
 for (const call of assistant.content) {
  if (call.type !== 'toolCall') continue;
  if (calls.has(call.id)) fail();
  calls.set(call.id,call.name);
 }
 const seen = new Set<string>();
 for (const result of context.messages.slice(n)) {
  if (result.role !== 'toolResult' || calls.get(result.toolCallId) !== result.toolName || seen.has(result.toolCallId)) fail();
  seen.add(result.toolCallId);
 }
 if (seen.size !== calls.size) fail();
 // Validate everything before factory allocation or mutation. Never overwrite a
 // live identity/transport container; this import is fresh-worker-only.
 if (state.size) fail();
 createOpenAICodexCompatibilityMetadata({providerSessionState:state,sessionId,requestKind:'prewarm'});
 const owner = state.get('openai-codex-responses') as Owner;
 Object.assign(owner.metadataSessions.get(sessionId)!, input.identity);
}
export function exportCheckpoint(state:Map<string,ProviderSessionState>, selector:string, sessionId:string, boundary:Context):Checkpoint {
 pin();
 const owner = state.get('openai-codex-responses') as Owner | undefined;
 const entry = owner?.metadataSessions.get(sessionId);
 if (!entry || !entry.turnId || !Number.isSafeInteger(entry.turnStartedAtUnixMs)) fail();
 const identity = Object.fromEntries(fields.map(key => [key,entry[key]])) as Identity;
 return {schema:1,providerDigest,selector,sessionId,boundaryCount:boundary.messages.length,boundaryDigest:digest(boundary),identity};
}
