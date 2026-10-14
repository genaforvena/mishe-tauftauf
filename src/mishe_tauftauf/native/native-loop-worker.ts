import {withNativeSession} from './native-session.ts';
import type {FetchLedger} from './fetch-budget.ts';
import {assertReadOnlyObservationContext, resolveTurnFetchAllowance} from './fetch-budget.ts';

// NDJSON: open, sequential numbered turns, close. Parent owns process-group,
// wall-clock and total-output limits. Never replay or retry a failed session.
async function* requests() {
 const reader = Bun.stdin.stream().getReader();
 const decoder = new TextDecoder('utf-8', {fatal:true});
 let pending = '';
 try {
  while (true) {
   const {done,value} = await reader.read();
   pending += done ? decoder.decode() : decoder.decode(value,{stream:true});
   let end: number;
   while ((end = pending.indexOf('\n')) >= 0) {
    if (end > 4 * 1024 * 1024) throw new Error('request line too large');
    const line = pending.slice(0,end); pending = pending.slice(end+1);
    yield JSON.parse(line);
   }
   if (pending.length > 4 * 1024 * 1024) throw new Error('request line too large');
   if (done) {if (pending) throw new Error('truncated request'); return;}
  }
 } finally {reader.releaseLock();}
}
const emit = (frame: unknown) => console.log(JSON.stringify(frame));
try {
 const input = requests();
 const first = await input.next();
 const open = first.value;
if (first.done || !open || open.type !== 'open' || typeof open.selector !== 'string' || typeof open.sessionId !== 'string') throw new Error('open request required');
const reasoning = typeof open.reasoning === 'string' ? open.reasoning : 'medium';
// A granted allowance is a contract, not a hint: resolve it (and refuse every
// broader or non-read-only configuration) before the first provider turn.
const allowance = resolveTurnFetchAllowance({allowance: open.fetchAllowance, workClass: open.workClass});
let closed = false;
await withNativeSession(open.selector, open.sessionId, async session => {
  emit({type:'ready'});
  let next = 1;
  for await (const request of input) {
   if (!request || typeof request !== 'object') throw new Error('request object required');
   if (request.type === 'close') {closed = true; break;}
   if (request.type !== 'turn' || request.id !== next) throw new Error('sequential turn ID required');
   // While an allowance above the default is granted, the read-only class is
   // re-checked structurally on every turn context: no other capability may
   // ride along under the read-only allowance.
   if (allowance.allowance > 1) assertReadOnlyObservationContext(request.context);
   let frame: unknown;
   try {
    frame = await session.turn(request.context, undefined, request.checkpoint);
   } catch (error) {
    // A turn that fails closed still claimed real lower-level dispatches: the
    // budget's row rides the diagnostic (attachFetchLedger in fetch-budget.ts)
    // and this frame matches the parent's `turn-error` receipt, so the caller
    // journal records durable evidence instead of exit prose. The exception
    // is rethrown unchanged: no turn failure becomes a completion.
    const row = (error as {fetch_ledger?: unknown} | null)?.fetch_ledger;
    emit({type:'turn-error', id:next,
     message: error instanceof Error ? error.message : String(error),
     fetch_ledger: row && typeof row === 'object' ? row as FetchLedger : null});
    throw error;
   }
   emit({type:'turn',id:next,frame});
   next++;
  }
  if (!closed) throw new Error('input ended before explicit close');
 }, reasoning, allowance);
 // Receipt proves both provider-state disposal and auth close returned.
 emit({type:'closed'});
 process.exit(0);
} catch (error) {
 console.error(error instanceof Error ? error.message : String(error));
 process.exit(1);
}
