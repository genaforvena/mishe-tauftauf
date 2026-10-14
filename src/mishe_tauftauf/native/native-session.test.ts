// Deterministic session-seam tests with a fully mocked provider: the per-turn
// dispatch ledger and cancellation across a second dispatch. No network and no
// provider behavior beyond the mocked module boundary, so the retry bound and
// its cancellation semantics stay checkable offline.
import {test, expect, mock} from 'bun:test';
import type {Context} from '@oh-my-pi/pi-ai/types';
import {READ_ONLY_CI_OBSERVATION_CLASS} from './fetch-budget.ts';

let fetchLog: string[] = [];
let scripted: Array<() => Response> = [];
// The provider transport waits on this gate between its own transient retry
// attempts, so caller-side cancellation can land between dispatches.
let retryGate = new Gate();
let firstDispatch = Promise.withResolvers<void>();
let capturedSignal: AbortSignal | null = null;

class Gate {
  #promise: Promise<void>;
  #resolve: () => void;
  constructor() {
   const resolvers = Promise.withResolvers<void>();
   this.#promise = resolvers.promise;
   this.#resolve = resolvers.resolve;
  }
  open(): void {this.#resolve();}
  wait(): Promise<void> {return this.#promise;}
}

mock.module('./native-bootstrap.ts', () => ({
  withNativeModel: async (_selector: string,
   consume: (environment: unknown) => Promise<unknown>) =>
   consume({registry: {resolver: () => 'seam-key'}, model: {id: 'seam-model'}}),
}));

// The provider transport's own transient retry re-issues the lower-level fetch
// between attempts; that is exactly what the allowance counts. The mock also
// waits on the gate before the retry so caller-side cancellation can land while
// the transport is between dispatches.
mock.module('@oh-my-pi/pi-ai/stream', () => ({
  streamSimple: (_model: unknown, _context: unknown,
   options: {fetch: typeof fetch; signal: AbortSignal}) => {
   capturedSignal = options.signal;
   return {
    async *[Symbol.asyncIterator]() {
     let response = await options.fetch('https://provider.test/turn', {});
     yield {type: 'start', status: response.status};
     if (response.status === 429) {
      await retryGate.wait();
      response = await options.fetch('https://provider.test/turn', {});
      yield {type: 'retry', status: response.status};
     }
     yield {type: 'done'};
    },
    result: async () => ({responseId: 'resp-seam', stopReason: 'stop'}),
   };
  },
}));

const realFetch = globalThis.fetch;

function scriptLowerLevelFetches(responses: Array<[number, string]>): void {
  fetchLog = [];
  retryGate = new Gate();
  firstDispatch = Promise.withResolvers<void>();
  scripted = responses.map(([status, statusText]) =>
   () => new Response(status === 200 ? 'ok' : 'rate limited', {status, statusText}));
  globalThis.fetch = (async (input: RequestInfo | URL) => {
   // The first dispatch is the deterministic signal the tests await, never a
   // guessed delay.
   if (fetchLog.length === 0) firstDispatch.resolve();
   fetchLog.push(String(input));
   const next = scripted.shift();
   if (!next) throw new Error('unscripted lower-level fetch');
   return next();
  }) as typeof fetch;
}

function restoreFetch(): void {
  globalThis.fetch = realFetch;
}

const context = {messages: [{role: 'user', content: [{type: 'text', text: 'seam turn'}]}]} as unknown as Context;

// The session module imports the provider stream at load time, so it is loaded
// lazily inside each test: the mocked provider stream then resolves without the
// pinned OMP node_modules on the bun path, keeping this test deterministic.
// Static import cannot work here because the mocked provider module must be
// registered before the session module's import graph resolves.
import type * as NativeSessionModule from './native-session.ts';
let withNativeSession: NativeSessionModule['withNativeSession'];
async function loadSession(): Promise<void> {
  withNativeSession ??= (await import('./native-session.ts')).withNativeSession;
}

test('a survived transient 429 leaves per-turn ledger evidence of both dispatches', async () => {
  scriptLowerLevelFetches([[429, 'Too Many Requests'], [200, 'OK']]);
  await loadSession();
  try {
    retryGate.open();
    const turned = await withNativeSession('fixture/model', 'seam-session',
     async session => session.turn(context), 'medium',
     {allowance: 2, workClass: READ_ONLY_CI_OBSERVATION_CLASS});
    const result = turned as {fetch_ledger: Record<string, unknown>};
    expect(fetchLog.length).toBe(2);
    expect(result.fetch_ledger).toEqual({allowance: 2,
     workClass: READ_ONLY_CI_OBSERVATION_CLASS, dispatches: 2,
     upstream_failure: {kind: 'status', status: 429, statusText: 'Too Many Requests'}});
  } finally {
    restoreFetch();
  }
});

test('cancellation during the transport wait refuses the second dispatch', async () => {
  scriptLowerLevelFetches([[429, 'Too Many Requests'], [200, 'OK']]);
  await loadSession();
  try {
    const controller = new AbortController();
    const pending = withNativeSession('fixture/model', 'seam-session',
     async session => session.turn(context, controller.signal), 'medium',
     {allowance: 2, workClass: READ_ONLY_CI_OBSERVATION_CLASS});
    // The first dispatch is claimed and the 429 observed; the caller cancels
    // while the provider transport is still waiting to retry.
    await firstDispatch.promise;
    controller.abort();
    retryGate.open();
    await expect(pending).rejects.toThrow('native turn cancelled before fetch');
    // The second dispatch was refused as cancelled, never claimed against the
    // allowance and never sent, so no turn result exists to fabricate a ledger
    // row from.
    expect(fetchLog.length).toBe(1);
    expect(capturedSignal?.aborted).toBe(true);
  } finally {
    restoreFetch();
  }
});
