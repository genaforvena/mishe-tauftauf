// Pure wire observation for the two streaming shapes the packaged native
// session sees. No provider import: this file is the deterministic seam that
// both the bundled session and `bun test` compile, so usage capture stays
// checkable offline. A frame this module cannot classify stays NO_FRAME —
// unread input is never upgraded into synthesized usage or a terminal.

export interface WireTerminal {event: string; response_id: string | null}

const RESPONSES_OPENING = 'response.created';
// Responses-API frames that close a payload.
const RESPONSES_TERMINALS = ['response.completed', 'response.done', 'response.incomplete',
 'response.failed', 'error'];
// Failed payloads carry no account-worthy usage; reporting one would fabricate
// a completion the server never returned.
const RESPONSES_USAGE_LOSSY = ['response.failed', 'error'];

export interface WireFrameView {
 /** Set only when this frame closes the payload. */
 terminal: WireTerminal | null;
 /** Set only when this frame opens a payload, discarding any earlier state. */
 opening: boolean;
 /** Response id this frame carried, opening or terminal. */
 response_id: string | null;
 /** Raw usage this frame reported, or null. */
 usage: Record<string, unknown> | null;
}

export interface SseFrameLike {event?: string | null; data?: unknown}

const NO_FRAME: WireFrameView = {terminal: null, opening: false, response_id: null, usage: null};

/** Narrowing guards over untrusted wire values; null means "not present". */
const asRecord = (value: unknown): Record<string, unknown> | null =>
 !value || typeof value !== 'object' || Array.isArray(value) ? null : value as Record<string, unknown>;
const asText = (value: unknown): string | null => typeof value === 'string' && value ? value : null;

/** Classify one decoded SSE frame into an opening, a terminal, or nothing. */
export function observeSseFrame(event: SseFrameLike | null | undefined): WireFrameView {
 if (!event) return NO_FRAME;
 const data = event.data;
 // `[DONE]` carries no fields; the recorder closes it from stream state.
 if (typeof data !== 'string' || !data || data === '[DONE]') return NO_FRAME;
 let parsed: unknown;
 try {parsed = JSON.parse(data);} catch {return NO_FRAME;}
 const frame = asRecord(parsed);
 if (!frame) return NO_FRAME;
 const type = asText(frame.type);
 return type ? observeResponsesFrame(type, frame) : observeCompletionFrame(frame);
}

function observeResponsesFrame(type: string, frame: Record<string, unknown>): WireFrameView {
 const response = asRecord(frame.response);
 const responseId = response ? asText(response.id) : null;
 if (type === RESPONSES_OPENING) return {terminal: null, opening: true, response_id: responseId, usage: null};
 if (!RESPONSES_TERMINALS.includes(type)) return NO_FRAME;
 const terminal = {event: type, response_id: responseId};
 // A failed payload, or one without a `response`, keeps usage null; the copy
 // keeps the caller from sharing a mutable reference with the decoder buffer.
 const payload = response && !RESPONSES_USAGE_LOSSY.includes(type) ? asRecord(response.usage) : null;
 return {terminal, opening: false, response_id: responseId, usage: payload ? Object.fromEntries(Object.entries(payload)) : null};
}

// openai-completions route: flat chat-completion chunks. `id` is top level and
// shared by every chunk of one payload; `usage` arrives on the trailing
// usage-only chunk that follows the finish_reason frame.
function observeCompletionFrame(frame: Record<string, unknown>): WireFrameView {
 const id = asText(frame.id);
 const finish = asText(Array.isArray(frame.choices) ? asRecord(frame.choices[0])?.finish_reason : null);
 const payload = asRecord(frame.usage);
 if (!id && !finish && !payload) return NO_FRAME;
 const usage = payload ? Object.fromEntries(Object.entries(payload)) : null;
 if (!finish) return {terminal: null, opening: false, response_id: id, usage};
 return {terminal: {event: 'chat.completion.finish_reason', response_id: id}, opening: false,
  response_id: id, usage};
}

export interface WireRecord {created_id: string | null; terminal: WireTerminal | null; usage: Record<string, unknown> | null}

/** Per-payload wire state. Reset for each native payload opening. */
export class WireRecorder {
 private created: string | null = null;
 private terminal: WireTerminal | null = null;
 private usage: Record<string, unknown> | null = null;

 reset(): void {this.created = null; this.terminal = null; this.usage = null;}

 observe(event: SseFrameLike | null | undefined): void {
  const data = event?.data;
  // The sentinel is server agreement to close; it carries no frame fields, so
  // the already-observed id stays the correlated one.
  if (data === '[DONE]' && !this.terminal) {
   this.terminal = {event: 'done', response_id: this.created};
   return;
  }
  const view = observeSseFrame(event);
  // A second opening inside one payload reopens it: the earlier terminal and
  // usage belong to the discarded attempt.
  if (view.opening) {this.terminal = null; this.usage = null;}
  if (view.terminal) {this.terminal = view.terminal; this.usage = null;}
  if (view.response_id && !this.created) this.created = view.response_id;
  // A later frame without usage must not erase usage a terminal already claimed.
  if (view.usage) this.usage = view.usage;
 }

 read(): WireRecord {
  return {created_id: this.created, terminal: this.terminal, usage: this.usage};
 }
}
