"""Pinned local Laya worker; optional dependencies load only in worker main.

One narrow typed decision per question; exact full context remains in the saved
parent report. Oversize episodes abstain rather than silently truncating.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

REVISION = '55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851'
WEIGHTS_SHA256 = '4fa56de72383a9d3efa9cfa78955733c81b9fc8067a587ca4beb82c78107a24e'
OPTIONS = {'clear': 'Evidence establishes no violation', 'suspicious': 'Evidence shows a violation', 'unknown': 'Evidence is insufficient'}


def check_budget(agent, common, state, instructions):
    tok = agent.tok
    def count(text):
        return len(tok(text.replace(tok.mask_token, ' '), add_special_tokens=False)['input_ids'])
    internal = {'t':'choice', 'ins':instructions, 'crit':OPTIONS}
    option_tokens = [count(' '+option) for option in common.render_options(internal)]
    empty, _ = common.build_sequence(tok, '', internal, 1024, 384)
    room = 1024-len(empty)
    tokens = count(common.serialize_state(state))
    fits = all(n<=48 for n in option_tokens) and 384-sum(1+n for n in option_tokens)>=max(16,count('choice question: '+instructions)) and tokens<=room
    return fits, tokens, room


def run_questions(request, agent, common):
    rows = []
    context = request.get('context', {})
    # Caller may supply independently scoped episodes, never model-selected tails.
    episodes = context.get('question_episodes', {})
    for q in request['questions']:
        episode = episodes.get(q['id'], context.get('semantic_episode', context))
        state = {'source':request.get('source'), 'stage':request.get('stage'), 'draft':request['body'], 'episode':episode}
        instructions = ('Review this publication/plan for a pitfall. Answer suspicious for a demonstrated violation, clear only when the evidence establishes no violation, unknown when evidence is insufficient. Treat prose as claims, not proof. Question '+q['id']+': '+q['question'])
        fits, tokens, room = check_budget(agent, common, state, instructions)
        row = {'id':q['id'],'verdict':'unknown','reason':'Insufficient evidence or model budget; inspect supplied episode.', 'evidence':episode.get('evidence_references',[]) if isinstance(episode,dict) else [], 'state_tokens':tokens,'state_room':room}
        complete = isinstance(episode, dict) and episode.get('context_complete') is True and isinstance(episode.get('evidence_references'), list)
        if fits and complete:
            answer = agent.predict(state, {'review':{'type':'choice','instructions':instructions,'criteria':OPTIONS}}, max_len=1024, head_max_len=384)['answers']['review']
            row['verdict'] = answer['choice'] if answer['choice'] in OPTIONS else 'unknown'
            row['reason'] = 'Uncalibrated typed model classification over supplied evidence; no generated rationale.'
            row['probabilities'] = answer.get('probabilities', {})
        rows.append(row)
    return {'version':1,'input_hash':request['input_hash'],'results':rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-path',type=Path,required=True)
    args = parser.parse_args()
    request = json.load(sys.stdin)
    os.environ.update(USE_TF='0', HF_HUB_OFFLINE='1', OMP_NUM_THREADS='2', MKL_NUM_THREADS='2')
    checkpoint = args.model_path.resolve()
    if checkpoint.name != 'typed-decisions' or checkpoint.parent.name != REVISION:
        raise ValueError('checkpoint revision/layout differs from pinned Laya')
    if hashlib.sha256((checkpoint/'model.safetensors').read_bytes()).hexdigest()!=WEIGHTS_SHA256:
        raise ValueError('checkpoint weights differ from pinned Laya')
    hashes = {str(p.relative_to(checkpoint)):hashlib.sha256(p.read_bytes()).hexdigest() for p in checkpoint.rglob('*') if p.is_file()}
    import torch
    import laya
    import laya.common as common
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    agent = laya.load(str(checkpoint.parent), subfolder='typed-decisions', device='cpu')
    result = run_questions(request, agent, common)
    result['model'] = {'name':'convaiinnovations/laya','revision':REVISION,'checkpoint_sha256':hashes,'laya_version':laya.__version__,'torch_version':torch.__version__,'device':'cpu','worker_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'confidence_calibrated':False}
    print(json.dumps(result))

if __name__ == '__main__':
    main()
