// Deterministic seam for the bounded per-turn provider fetch allowance. No
// provider import, no network: config resolution, the per-dispatch counter,
// the transient-success path and the distinct exhaustion diagnostic.
import {test, expect} from 'bun:test';
import {DEFAULT_TURN_FETCH_ALLOWANCE, MAX_TURN_FETCH_ALLOWANCE,
 READ_ONLY_CI_OBSERVATION_CLASS, MUTATING_PUBLICATION_CLASS, TurnFetchBudget, assertReadOnlyObservationContext,
 assertMutatingPublicationContext, assertTurnFetchAllowance, isTransientUpstreamStatus, resolveTurnFetchAllowance} from './fetch-budget.ts';

const CLASS = READ_ONLY_CI_OBSERVATION_CLASS;

test('absent configuration keeps the unchanged one-fetch default', () => {
  expect(resolveTurnFetchAllowance({})).toEqual({allowance: DEFAULT_TURN_FETCH_ALLOWANCE, workClass: ''});
  expect(DEFAULT_TURN_FETCH_ALLOWANCE).toBe(1);
});

test('the named read-only class accepts the bounded two-fetch allowance', () => {
  expect(resolveTurnFetchAllowance({allowance: 2, workClass: CLASS}))
   .toEqual({allowance: 2, workClass: CLASS});
  expect(MAX_TURN_FETCH_ALLOWANCE).toBe(2);
});

test('a broader allowance is refused, never coerced', () => {
  for (const allowance of [3, 6, 0, -1, 1.5, '2', true, null]) {
   expect(() => resolveTurnFetchAllowance({allowance, workClass: CLASS}))
    .toThrow('provider fetch allowance must be an integer 1..2');
  }
});

test('a non-read-only class is refused even at the bounded allowance', () => {
  for (const workClass of ['', 'other-class', 'Experiment-2-Read-Only-CI-Observation', 2, null]) {
   expect(() => resolveTurnFetchAllowance({allowance: 2, workClass}))
    .toThrow(`fetch allowance is limited to the ${CLASS} read-only class`);
  }
});

test('a half-specified pair is refused', () => {
  expect(() => resolveTurnFetchAllowance({allowance: 2})).toThrow('requires both');
  expect(() => resolveTurnFetchAllowance({workClass: CLASS})).toThrow('requires both');
});

test('every lower-level dispatch counts against the per-turn bound', () => {
  const budget = new TurnFetchBudget(1);
  expect(budget.claim()).toBe(true);
  expect(budget.claim()).toBe(false);
  expect(budget.spent).toBe(1);

  const raised = new TurnFetchBudget(2, CLASS);
  expect(raised.claim()).toBe(true);
  expect(raised.claim()).toBe(true);
  expect(raised.claim()).toBe(false);
  expect(raised.spent).toBe(2);
  expect(raised.allowance).toBe(2);
});

test('a transient 429 then success is the granted transient-success path', () => {
  const budget = new TurnFetchBudget(2, CLASS);
  expect(budget.claim()).toBe(true);
  budget.observeResponse(429, 'Too Many Requests');
  // The second dispatch is the granted retry, not a replay of tools or effects.
  expect(budget.claim()).toBe(true);
  budget.observeResponse(200, 'OK');
  // The bounded turn is spent; a third dispatch would be a broader allowance.
  expect(budget.claim()).toBe(false);
  expect(budget.spent).toBe(2);
});

test('exhaustion after repeated transient failures keeps both facts distinct', () => {
  const budget = new TurnFetchBudget(2, CLASS);
  budget.claim();
  budget.observeResponse(429, 'Too Many Requests');
  budget.claim();
  budget.observeResponse(503, 'Service Unavailable');
  expect(budget.claim()).toBe(false);
  const error = budget.exhaustion();
  expect(error.message).toBe('native provider fetch budget exhausted after 2 of 2 fetches '
   + `permitted for ${CLASS}; upstream HTTP 503 Service Unavailable (transient)`);
});

test('exhaustion with no observed upstream failure says so instead of inventing one', () => {
  const budget = new TurnFetchBudget(1);
  budget.claim();
  expect(budget.claim()).toBe(false);
  expect(budget.exhaustion().message).toBe('native provider fetch budget exhausted after 1 of 1 '
   + 'fetches permitted for the default class; no upstream failure observed');
});

test('a network-level failure is preserved as its own named fact', () => {
  const budget = new TurnFetchBudget(2, CLASS);
  budget.claim();
  budget.observeNetworkError(new Error('ECONNRESET'));
  budget.claim();
  budget.observeNetworkError('socket closed');
  expect(budget.claim()).toBe(false);
  expect(budget.exhaustion().message).toContain('upstream network error: socket closed');
});

test('only the transient status classes are recorded as upstream failures', () => {
  expect([408, 429, 500, 502, 503, 504].every(isTransientUpstreamStatus)).toBe(true);
  expect([200, 201, 400, 401, 403, 404, 422].some(isTransientUpstreamStatus)).toBe(false);
  const budget = new TurnFetchBudget(2, CLASS);
  budget.claim();
  budget.observeResponse(400, 'Bad Request');
  budget.claim();
  expect(budget.claim()).toBe(false);
  expect(budget.exhaustion().message).toContain('no upstream failure observed');
});

test('the allowance constructor range-checks its own argument', () => {
  expect(assertTurnFetchAllowance(2)).toBe(2);
  expect(() => new TurnFetchBudget(3)).toThrow('must be an integer 1..2');
});

test('a read-only turn context may name only frozen read-only capabilities', () => {
  const tool = {name: 'ci_status', parameters: {type: 'object'}};
  expect(() => assertReadOnlyObservationContext({messages: [], tools: [tool]})).not.toThrow();
  expect(() => assertReadOnlyObservationContext({messages: [], tools: []})).not.toThrow();
  expect(() => assertReadOnlyObservationContext({messages: [], tools: [tool, {name: 'write_file'}]}))
   .toThrow('capabilities outside ["ci_status"]: "write_file"');
  expect(() => assertReadOnlyObservationContext({messages: [], tools: [{name: 'CI_STATUS'}]}))
   .toThrow('capabilities outside ["ci_status"]');
  expect(() => assertReadOnlyObservationContext({messages: []})).toThrow('tools array');
});

test('the mutating publication class accepts only the default allowance', () => {
  expect(resolveTurnFetchAllowance({allowance: 1, workClass: MUTATING_PUBLICATION_CLASS}))
   .toEqual({allowance: 1, workClass: MUTATING_PUBLICATION_CLASS});
  expect(() => resolveTurnFetchAllowance({allowance: 2, workClass: MUTATING_PUBLICATION_CLASS}))
   .toThrow('default 1-fetch allowance only');
});

test('a mutating publication context refuses a raised allowance', () => {
  const ctx = {tools: [{name: 'publish'}]};
  expect(() => assertMutatingPublicationContext(ctx, 2)).toThrow('mutating publication context');
  expect(() => assertMutatingPublicationContext(ctx, 1)).not.toThrow();
  expect(() => assertMutatingPublicationContext({tools: [{name: 'ci_status'}]}, 2)).not.toThrow();
});
