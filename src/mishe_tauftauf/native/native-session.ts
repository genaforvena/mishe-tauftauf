import {streamSimple} from '@oh-my-pi/pi-ai/stream';
import type {Context, ProviderSessionState} from '@oh-my-pi/pi-ai/types';
import {withNativeModel} from './native-bootstrap.ts';
import {restoreCheckpoint} from './native-checkpoint.ts';
import {WireRecorder} from './wire-usage.ts';
import {DEFAULT_TURN_FETCH_ALLOWANCE, TurnFetchBudget} from './fetch-budget.ts';

// One underlying fetch per turn by default, including lower-level HTTP retries
// and encoding fallback. This deliberately withholds automatic retry authority
// (docs/how-it-works.md). The only permitted raise is the operator-authorized
// read-only CI-observation allowance of at most two fetches per turn, resolved
// and range-checked in fetch-budget.ts; every other configuration is refused.

// One owned subprocess lifetime. Bootstrap rejection is fatal to its caller;
// never reacquire/retry in that process (pre-return auth acquisition gap).
export async function withNativeSession<T>(selector: string, sessionId: string,
 consume: (session: {turn: (context: Context, signal?: AbortSignal, checkpoint?: unknown) => Promise<unknown>}) => Promise<T>,
 reasoning: string = 'medium',
 allowanceConfig: {allowance: number; workClass: string} = {allowance: DEFAULT_TURN_FETCH_ALLOWANCE, workClass: ''}): Promise<T> {
 if (typeof sessionId !== 'string' || !sessionId.trim() || sessionId !== sessionId.trim()) throw new Error('normalized stable sessionId required');
 const allowance = new TurnFetchBudget(allowanceConfig.allowance, allowanceConfig.workClass).allowance;
 return withNativeModel(selector, async ({registry, model}) => {
  const state = new Map<string, ProviderSessionState>();
  let closed = false;
  let poisoned = false;
  let active: Promise<unknown> | null = null;
  const controller = new AbortController();
  async function generate(context: Context) {
  // Bound all provider retry layers, including encoding fallback. The
  // allowance is fixed for the session lifetime; the read-only class check
  // and the bound's range check ran before any provider turn.
  const budget = new TurnFetchBudget(allowance, allowanceConfig.workClass);
  let fetchBudgetError: Error | null = null;
  const fetchOnce: typeof fetch = async (input, init) => {
   if (controller.signal.aborted) throw new Error('native turn cancelled before fetch');
   if (!budget.claim()) {
    poisoned = true;
    fetchBudgetError = budget.exhaustion();
    controller.abort(fetchBudgetError);
    throw fetchBudgetError;
   }
   try {
    const response = await fetch(input, init);
    budget.observeResponse(response.status, response.statusText);
    return response;
   } catch (error) {
    budget.observeNetworkError(error);
    throw error;
   }
  };
  const wire = new WireRecorder();
  const stream = streamSimple(model, context, {
   apiKey: registry.resolver(model, sessionId),
   sessionId: sessionId,
   reasoning: reasoning,
   providerSessionState: state,
   preferWebsockets: false, // Checkpoints cover exact full-history SSE only.
   signal: controller.signal,
   fetch: fetchOnce,
   codexSseMaxAttempts: 1,
   // Reset for each native payload opening, not each lower-level HTTP retry.
   onPayload: () => {wire.reset();},
   onSseEvent: event => {wire.observe(event);},
  });
  let terminal: string | undefined;
  for await (const event of stream) terminal = event.type;
  const assistant = await stream.result();
  // The provider may normalize our abort into ordinary cancellation.
  if (fetchBudgetError) throw fetchBudgetError;
  if (terminal !== 'done' && terminal !== 'error') throw new Error('native stream ended without terminal event');
  // Native normalization is not evidence of raw usage or response correlation.
  const observed = wire.read();
  const responseId = observed.terminal?.response_id ?? null;
  if (responseId && ((observed.created_id && observed.created_id !== responseId) || (assistant.responseId && assistant.responseId !== responseId))) {
   throw new Error('wire terminal response ID mismatch');
  }
  const correlated = Boolean(responseId && assistant.responseId === responseId);
  // No checkpoint on the openai-responses route: single-turn turns only.
  // exportCheckpoint/restoreCheckpoint refuse loudly; continuation without a
  // checkpoint is a fresh full-history turn, not a resumed provider turn.
  const checkpoint = null;
  return {terminal, assistant, checkpoint, wire_usage: correlated ? observed.usage : null,
   wire_terminal: observed.terminal ? {...observed.terminal, created_response_id: observed.created_id,
    assistant_response_id: assistant.responseId ?? null, correlated, stop_reason: assistant.stopReason} : null};
  }
  const session = {async turn(context: Context, signal?: AbortSignal, checkpoint?: unknown): Promise<unknown> {
   if (closed || poisoned) throw new Error('native session unavailable');
   if (active) throw new Error('concurrent native turn prohibited');
   if (!context || !Array.isArray(context.messages)) throw new Error('native context messages required');
   if (checkpoint !== undefined) restoreCheckpoint(state, selector, sessionId, context, checkpoint);
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
