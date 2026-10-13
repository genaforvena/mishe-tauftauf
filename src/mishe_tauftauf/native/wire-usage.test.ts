// Deterministic seam for the wire observer. No provider import, no network:
// every case below is a captured-shape frame or a malformed one.
import {test, expect} from 'bun:test';
import {observeSseFrame, WireRecorder} from './wire-usage.ts';

const frame = (data: unknown) => ({data: typeof data === 'string' ? data : JSON.stringify(data)});

test('responses opening records the id without usage or a terminal', () => {
  expect(observeSseFrame(frame({type: 'response.created', response: {id: 'resp_1'}}))).toEqual({
    terminal: null, opening: true, response_id: 'resp_1', usage: null});
});

test('responses completed carries the terminal id and the raw usage', () => {
  expect(observeSseFrame(frame({type: 'response.completed',
   response: {id: 'resp_1', usage: {input_tokens: 12, output_tokens: 3}}}))).toEqual({
    terminal: {event: 'response.completed', response_id: 'resp_1'}, opening: false,
    response_id: 'resp_1', usage: {input_tokens: 12, output_tokens: 3}});
});

test('responses completed without usage keeps usage unknown', () => {
  expect(observeSseFrame(frame({type: 'response.completed', response: {id: 'resp_1'}}))).toEqual({
    terminal: {event: 'response.completed', response_id: 'resp_1'}, opening: false,
    response_id: 'resp_1', usage: null});
});

test('a failed payload never reports usage', () => {
  expect(observeSseFrame(frame({type: 'response.failed',
   response: {id: 'resp_1', usage: {input_tokens: 1}}}))).toEqual({
    terminal: {event: 'response.failed', response_id: 'resp_1'}, opening: false,
    response_id: 'resp_1', usage: null});
  expect(observeSseFrame(frame({type: 'error', response: {id: 'resp_1', usage: {input_tokens: 1}}})))
   .toEqual({terminal: {event: 'error', response_id: 'resp_1'}, opening: false,
             response_id: 'resp_1', usage: null});
});

test('an unrecognized responses frame is not an observation', () => {
  expect(observeSseFrame(frame({type: 'response.output_text.delta', delta: 'x'})))
   .toEqual({terminal: null, opening: false, response_id: null, usage: null});
});

test('a typed frame that is not responses still reports no usage', () => {
  // Some openai-compatible hosts put a `type` on a chunk carrying usage; without
  // a recognised responses type this stays unknown rather than misattributed.
  expect(observeSseFrame(frame({type: 'chunk', id: 'chatcmpl_1', usage: {input_tokens: 4}})))
   .toEqual({terminal: null, opening: false, response_id: null, usage: null});
});

test('completion chunk records the shared id and the finish terminal', () => {
  expect(observeSseFrame(frame({object: 'chat.completion.chunk', id: 'chatcmpl_1',
   choices: [{index: 0, delta: {content: 'pong'}, finish_reason: null}]})))
   .toEqual({terminal: null, opening: false, response_id: 'chatcmpl_1', usage: null});
  expect(observeSseFrame(frame({object: 'chat.completion.chunk', id: 'chatcmpl_1',
   choices: [{index: 0, delta: {}, finish_reason: 'stop'}]}))).toEqual({
    terminal: {event: 'chat.completion.finish_reason', response_id: 'chatcmpl_1'}, opening: false,
    response_id: 'chatcmpl_1', usage: null});
});

test('the trailing usage-only chunk is kept after the finish terminal', () => {
  const recorder = new WireRecorder();
  recorder.observe(frame({object: 'chat.completion.chunk', id: 'chatcmpl_1',
   choices: [{index: 0, delta: {}, finish_reason: 'stop'}]}));
  recorder.observe(frame({object: 'chat.completion.chunk', id: 'chatcmpl_1', choices: [],
   usage: {prompt_tokens: 7, completion_tokens: 1, total_tokens: 8}}));
  expect(recorder.read()).toEqual({
    created_id: 'chatcmpl_1',
    terminal: {event: 'chat.completion.finish_reason', response_id: 'chatcmpl_1'},
    usage: {prompt_tokens: 7, completion_tokens: 1, total_tokens: 8}});
});

test('a done sentinel closes a stream that never sent finish_reason', () => {
  const recorder = new WireRecorder();
  recorder.observe(frame({object: 'chat.completion.chunk', id: 'chatcmpl_2',
   choices: [{index: 0, delta: {content: 'pong'}, finish_reason: null}]}));
  recorder.observe({data: '[DONE]'});
  expect(recorder.read()).toEqual({
    created_id: 'chatcmpl_2', terminal: {event: 'done', response_id: 'chatcmpl_2'}, usage: null});
});

test('a repeated opening discards the earlier terminal and usage', () => {
  const recorder = new WireRecorder();
  recorder.observe(frame({type: 'response.completed', response: {id: 'resp_1', usage: {input_tokens: 5}}}));
  recorder.observe(frame({type: 'response.created', response: {id: 'resp_2'}}));
  expect(recorder.read()).toEqual({created_id: 'resp_1', terminal: null, usage: null});
  recorder.observe(frame({type: 'response.completed', response: {id: 'resp_2', usage: {input_tokens: 6}}}));
  expect(recorder.read()).toEqual({created_id: 'resp_1',
    terminal: {event: 'response.completed', response_id: 'resp_2'}, usage: {input_tokens: 6}});
});

test('a reset payload drops the previous wire state', () => {
  const recorder = new WireRecorder();
  recorder.observe(frame({type: 'response.completed', response: {id: 'resp_9', usage: {input_tokens: 5}}}));
  recorder.reset();
  expect(recorder.read()).toEqual({created_id: null, terminal: null, usage: null});
});

test('malformed and empty frames are unknown, never a synthesized terminal', () => {
  for (const data of [undefined, null, '', '[DONE]', 'not json', '[]', 'null', '7']) {
    expect(observeSseFrame({data})).toEqual({terminal: null, opening: false, response_id: null, usage: null});
  }
  expect(observeSseFrame(null)).toEqual({terminal: null, opening: false, response_id: null, usage: null});
});

test('a non-object usage is not an object usage', () => {
  for (const usage of [4, [], 'x', null, true]) {
    expect(observeSseFrame(frame({type: 'response.completed', response: {id: 'r', usage}})))
     .toEqual({terminal: {event: 'response.completed', response_id: 'r'}, opening: false,
               response_id: 'r', usage: null});
  }
  expect(observeSseFrame(frame({type: 'response.completed', response: {id: 'r', usage: {}}})))
   .toEqual({terminal: {event: 'response.completed', response_id: 'r'}, opening: false,
             response_id: 'r', usage: {}});
});
