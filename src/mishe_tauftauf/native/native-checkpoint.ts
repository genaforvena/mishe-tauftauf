import type {Context, ProviderSessionState} from '@oh-my-pi/pi-ai/types';

// Retired: the live fleet selector resolves to `openai-responses`, which keeps
// no CodexMetadataSessionState (measured wake 20526: export finds only
// `openai-responses:opencode-go` chain state, no metadataSessions). The whole-file
// byte pin (c35700fd…) also drifted (live bytes 675a56d2…). No checkpoint can be
// exported or restored on this route; callers refuse loudly instead of failing
// every turn at export. A future openai-codex-responses selector would need a
// fresh layout-gated adapter, not a re-pin of this file.
function fail():never {throw new Error('UNKNOWN: native checkpoint unsupported on openai-responses route');}
export function isContinuation(context:Context):boolean {
 const last = context.messages.findLast(message => message.role !== 'toolResult');
 return last?.role === 'assistant';
}
export function restoreCheckpoint(_state:Map<string,ProviderSessionState>, _selector:string, _sessionId:string, _context:Context, _input:unknown):void {
 fail();
}
export function exportCheckpoint(_state:Map<string,ProviderSessionState>, _selector:string, _sessionId:string, _boundary:Context):never {
 fail();
}
