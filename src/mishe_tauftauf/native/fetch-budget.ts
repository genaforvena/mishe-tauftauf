// Bounded per-turn provider fetch allowance. No provider import: this file is
// the deterministic seam both the bundled session and `bun test` compile, so
// the retry bound stays checkable offline.
//
// The default withholds automatic retry authority: exactly one underlying
// fetch per turn, counting every lower-level dispatch including encoding
// fallback (docs/how-it-works.md). The operator's narrow Experiment-2
// authorization permits at most two fetches per turn, only for the named
// read-only CI-observation class. Anything broader, or any other work class,
// is refused here — the refusal is durable evidence, not a silent fallback.

export const DEFAULT_TURN_FETCH_ALLOWANCE = 1;
export const MAX_TURN_FETCH_ALLOWANCE = 2;
export const READ_ONLY_CI_OBSERVATION_CLASS = 'experiment-2-read-only-ci-observation';
// The frozen Experiment-2 read-only capability set. A granted allowance never
// lets another capability ride along inside the same turn context.
export const READ_ONLY_CI_OBSERVATION_TOOLS = ['ci_status'];

export class FetchAllowanceError extends Error {}

/** Transient upstream statuses the provider transport retries on its own. */
export function isTransientUpstreamStatus(status: number): boolean {
 return status === 408 || status === 429 || (status >= 500 && status <= 599);
}

/** Validate a resolved allowance: integer, within the bounded range. */
export function assertTurnFetchAllowance(value: unknown): number {
 if (typeof value !== 'number' || !Number.isInteger(value)
     || value < DEFAULT_TURN_FETCH_ALLOWANCE || value > MAX_TURN_FETCH_ALLOWANCE) {
  throw new FetchAllowanceError('provider fetch allowance must be an integer '
   + `${DEFAULT_TURN_FETCH_ALLOWANCE}..${MAX_TURN_FETCH_ALLOWANCE}, got ${JSON.stringify(value)}`);
 }
 return value;
}

export interface TurnFetchAllowanceConfig {allowance: number; workClass: string}

/**
 * Resolve an open-request `(allowance, workClass)` pair. Both absent is the
 * unchanged default. Any explicit configuration must name the read-only
 * CI-observation class and stay within the bounded allowance; every other
 * shape — broader allowance, non-read-only class, or a half-specified pair —
 * is rejected, never coerced to the default.
 */
export function resolveTurnFetchAllowance(fields: {
 allowance?: unknown; workClass?: unknown}): TurnFetchAllowanceConfig {
 const {allowance, workClass} = fields;
 if (allowance === undefined && workClass === undefined) {
  return {allowance: DEFAULT_TURN_FETCH_ALLOWANCE, workClass: ''};
 }
 if (allowance === undefined || workClass === undefined) {
  throw new FetchAllowanceError('fetch allowance configuration requires both '
   + 'workClass and allowance');
 }
 if (workClass !== READ_ONLY_CI_OBSERVATION_CLASS) {
  throw new FetchAllowanceError('fetch allowance is limited to the '
   + `${READ_ONLY_CI_OBSERVATION_CLASS} read-only class, got ${JSON.stringify(workClass)}`);
 }
 return {allowance: assertTurnFetchAllowance(allowance), workClass};
}

const asRecord = (value: unknown): Record<string, unknown> | null =>
 !value || typeof value !== 'object' || Array.isArray(value) ? null : value as Record<string, unknown>;

/**
 * Structural read-only check for a turn context: while an allowance above the
 * default is granted, every named capability must be in the frozen read-only
 * set. An unnamed or foreign capability refuses the turn.
 */
export function assertReadOnlyObservationContext(context: unknown): void {
 const tools = asRecord(context)?.tools;
 if (!Array.isArray(tools)) {
  throw new FetchAllowanceError('read-only observation context must name a tools array');
 }
 const foreign = tools.filter(tool => {
  const name = asRecord(tool)?.name;
  return typeof name !== 'string' || !READ_ONLY_CI_OBSERVATION_TOOLS.includes(name);
 });
 if (foreign.length > 0) {
  const named = foreign.map(tool => JSON.stringify(asRecord(tool)?.name ?? null)).join(', ');
  throw new FetchAllowanceError(`read-only observation context names capabilities outside `
   + `${JSON.stringify(READ_ONLY_CI_OBSERVATION_TOOLS)}: ${named}`);
 }
}

export interface UpstreamFailure {
 kind: 'status' | 'network';
 status?: number;
 statusText?: string;
 message?: string;
}

/** Durable per-turn dispatch evidence: what was permitted, how many
 * lower-level dispatches were actually claimed, and the last transient
 * upstream failure observed (null when none). One row per turn in the caller
 * journal keeps upstream 408/429/5xx and budget exhaustion distinct facts. */
export interface FetchLedger {
 allowance: number;
 workClass: string;
 dispatches: number;
 upstream_failure: UpstreamFailure | null;
}

/** Per-turn dispatch counter. Every lower-level fetch counts, including
 * encoding fallback; the tool/effect layer is never retried by this bound. */
export class TurnFetchBudget {
 readonly allowance: number;
 readonly workClass: string;
 private fetches = 0;
 private lastFailure: UpstreamFailure | null = null;

 constructor(allowance: number, workClass = '') {
  this.allowance = assertTurnFetchAllowance(allowance);
  this.workClass = workClass;
 }

 get spent(): number {return this.fetches;}

 /** False when this dispatch would exceed the per-turn allowance. */
 claim(): boolean {
  if (this.fetches >= this.allowance) return false;
  this.fetches += 1;
  return true;
 }

 /** Record a transient upstream status; later non-transient statuses do not
  * erase the fact that the transport is mid-retry. */
 observeResponse(status: number, statusText: string): void {
  if (isTransientUpstreamStatus(status)) {
   this.lastFailure = {kind: 'status', status, statusText};
  }
 }

 observeNetworkError(error: unknown): void {
  this.lastFailure = {kind: 'network',
   message: error instanceof Error ? error.message : String(error)};
 }

 /**
  * Exhaustion diagnostic. Upstream 408/429/5xx and exhaustion itself stay
  * distinct, named facts so the caller journal can preserve both: one failed
  * sample never masquerades as a budget-only failure.
  */
 exhaustion(): Error {
  const failure = this.lastFailure;
  const detail = failure === null ? 'no upstream failure observed'
   : failure.kind === 'status'
     ? `upstream HTTP ${failure.status}${failure.statusText ? ` ${failure.statusText}` : ''} (transient)`
     : `upstream network error: ${failure.message}`;
  return new Error(`native provider fetch budget exhausted after ${this.fetches} of `
   + `${this.allowance} fetches permitted for ${this.workClass || 'the default class'}; ${detail}`);
 }

 /** Per-turn evidence for the caller journal. `dispatches` counts every
  * claimed lower-level fetch; `upstream_failure` is the preserved transient
  * fact (null when none), never merged into the exhaustion diagnostic. */
 ledger(): FetchLedger {
  return {allowance: this.allowance, workClass: this.workClass,
   dispatches: this.fetches, upstream_failure: this.lastFailure};
 }
}

/** Attach durable per-turn dispatch evidence to a thrown exhaustion error.
 * The turn still fails closed; the row rides the diagnostic so the worker
 * can emit it before the session ends, leaving the claims made before the
 * refusal auditable without trusting the exit prose for the count. */
export function attachFetchLedger<TError extends Error>(error: TError, ledger: FetchLedger): TError {
 (error as TError & {fetch_ledger?: FetchLedger}).fetch_ledger = ledger;
 return error;
}
