import {withNativeSession} from './native-session.ts';

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
 let closed = false;
 await withNativeSession(open.selector,open.sessionId, async session => {
  emit({type:'ready'});
  let next = 1;
  for await (const request of input) {
   if (!request || typeof request !== 'object') throw new Error('request object required');
   if (request.type === 'close') {closed = true; break;}
   if (request.type !== 'turn' || request.id !== next) throw new Error('sequential turn ID required');
   const frame = await session.turn(request.context);
   emit({type:'turn',id:next,frame});
   next++;
  }
  if (!closed) throw new Error('input ended before explicit close');
 });
 // Receipt proves both provider-state disposal and auth close returned.
 emit({type:'closed'});
 process.exit(0);
} catch (error) {
 console.error(error instanceof Error ? error.message : String(error));
 process.exit(1);
}
