import {streamSimple} from '@oh-my-pi/pi-ai/stream';
import type {Context, ProviderSessionState} from '@oh-my-pi/pi-ai/types';
import {withNativeModel} from './native-bootstrap.ts';
import {restoreCheckpoint, exportCheckpoint, isContinuation} from './native-checkpoint.ts';

// One owned subprocess lifetime. Bootstrap rejection is fatal to its caller;
// never reacquire/retry in that process (pre-return auth acquisition gap).
export async function withNativeSession<T>(selector: string, sessionId: string,
 consume: (session: {turn: (context: Context, signal?: AbortSignal, checkpoint?: unknown) => Promise<unknown>}) => Promise<T>): Promise<T> {
 if (typeof sessionId !== 'string' || !sessionId.trim() || sessionId !== sessionId.trim()) throw new Error('normalized stable sessionId required');
 return withNativeModel(selector, async ({registry, model}) => {
  const state = new Map<string, ProviderSessionState>();
  let closed = false;
  let poisoned = false;
  let active: Promise<unknown> | null = null;
  const controller = new AbortController();
  async function generate(context: Context) {
  // Bound all provider retry layers, including encoding fallback.
  let fetches = 0;
  let fetchBudgetError: Error | null = null;
  const fetchOnce: typeof fetch = async (input, init) => {
   if (controller.signal.aborted) throw new Error('native turn cancelled before fetch');
   if (fetches >= 1) {
    poisoned = true;
    fetchBudgetError = new Error('native provider fetch budget exhausted');
    controller.abort(fetchBudgetError);
    throw fetchBudgetError;
   }
   fetches++;
   return fetch(input, init);
  };
  let wireUsage: Record<string, unknown> | null = null;
  let createdId: string | null = null;
  let wireTerminal: {event: string; response_id: string | null} | null = null;
  const stream = streamSimple(model, context, {
   apiKey: registry.resolver(model, sessionId),
   sessionId: sessionId,
   reasoning: 'medium',
   providerSessionState: state,
   preferWebsockets: false, // Checkpoints cover exact full-history SSE only.
   signal: controller.signal,
   fetch: fetchOnce,
   codexSseMaxAttempts: 1,
   // Reset for each native payload opening, not each lower-level HTTP retry.
   onPayload: () => { wireUsage = null; createdId = null; wireTerminal = null; },
   onSseEvent: event => {
    let frame: unknown;
    try { frame = JSON.parse(event.data); } catch { return; }
    if (!frame || typeof frame !== 'object' || !('type' in frame)) return;
    const response = 'response' in frame && frame.response && typeof frame.response === 'object' ? frame.response : null;
    const id = response && 'id' in response && typeof response.id === 'string' ? response.id : null;
    if (frame.type === 'response.created') {
     createdId = id;
     wireUsage = null;
     wireTerminal = null;
     return;
    }
    if (!['response.completed', 'response.done', 'response.incomplete', 'response.failed', 'error'].includes(String(frame.type))) return;
    wireTerminal = {event: String(frame.type), response_id: id};
    wireUsage = null;
    if (frame.type === 'response.failed' || frame.type === 'error' || !response || !('usage' in response)) return;
    const usage = response.usage;
    if (usage && typeof usage === 'object' && !Array.isArray(usage)) {
     wireUsage = Object.fromEntries(Object.entries(usage));
    }
   },
  });
  let terminal: string | undefined;
  for await (const event of stream) terminal = event.type;
  const assistant = await stream.result();
  // The provider may normalize our abort into ordinary cancellation.
  if (fetchBudgetError) throw fetchBudgetError;
  if (terminal !== 'done' && terminal !== 'error') throw new Error('native stream ended without terminal event');
  // Native normalization is not evidence of raw usage or response correlation.
  const observed = wireTerminal as {event: string; response_id: string | null} | null;
  const responseId = observed?.response_id;
  if (responseId && ((createdId && createdId !== responseId) || (assistant.responseId && assistant.responseId !== responseId))) {
   throw new Error('wire terminal response ID mismatch');
  }
  const correlated = Boolean(responseId && assistant.responseId === responseId);
  const checkpoint = terminal === 'done' && correlated && observed?.event === 'response.completed'
   ? exportCheckpoint(state, selector, sessionId, {...context,messages:[...context.messages,assistant]}) : null;
  return {terminal, assistant, checkpoint, wire_usage: correlated ? wireUsage : null,
   wire_terminal: observed && {...observed, created_response_id: createdId,
    assistant_response_id: assistant.responseId ?? null, correlated, stop_reason: assistant.stopReason}};
  }
  const session = {async turn(context: Context, signal?: AbortSignal, checkpoint?: unknown): Promise<unknown> {
   if (closed || poisoned) throw new Error('native session unavailable');
   if (active) throw new Error('concurrent native turn prohibited');
   if (!context || !Array.isArray(context.messages)) throw new Error('native context messages required');
   if (checkpoint !== undefined) restoreCheckpoint(state, selector, sessionId, context, checkpoint);
   else if (!state.size && isContinuation(context)) throw new Error('UNKNOWN: continuation requires logical checkpoint');
   const abort = () => {poisoned = true; controller.abort(signal?.reason);};
   if (signal?.aborted) {abort(); throw new Error('native turn cancelled');}
   signal?.addEventListener('abort', abort, {once:true});
   try {
    active = generate(context);
    const result = await active;
    if (controller.signal.aborted) throw new Error('native turn cancelled');
    return result;
   } catch (error) {poisoned = true; throw error;}
   finally {active = null; signal?.removeEventListener('abort', abort);}
  }};
  try {return await consume(session);}
  finally {
   closed = true;
   controller.abort();
   // Drain even a caller-abandoned turn before disposing its provider state.
   if (active) await active.catch(() => {});
   const failures: unknown[] = [];
   for (const value of state.values()) {
    try {await value.close();} catch (error) {failures.push(error);}
   }
   state.clear();
   if (failures.length) throw new AggregateError(failures, 'native session disposal failed');
  }
 });
}
