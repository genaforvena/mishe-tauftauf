"""Local Laya worker for the optional analysis advisor's JSON protocol."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

REVISION = '55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851'
WEIGHTS_SHA256 = '4fa56de72383a9d3efa9cfa78955733c81b9fc8067a587ca4beb82c78107a24e'


def model_state(state):
    return {'meaning': 'Suggest investigation focus only. Read full log for verdicts; obligations still apply.',
            'context_complete': state.get('context_complete', False),
            'previous_completed_analysis': state.get('previous_completed_analysis', ''),
            'entries': [{k: entry[k] for k in ('sequence', 'source', 'text')} for entry in state.get('entries', [])]}


def choose(request, agent, common):
    state = model_state(request['state'])
    question = {'type': 'choice', 'instructions': request['question'], 'criteria': request['analyses']}
    internal = {'t': 'choice', 'ins': request['question'], 'crit': request['analyses']}
    tok = agent.tok
    def count(text):
        return len(tok(text.replace(tok.mask_token, ' '), add_special_tokens=False)['input_ids'])
    option_tokens = [count(' '+option) for option in common.render_options(internal)]
    empty, _ = common.build_sequence(tok, '', internal, 1024, 384)
    room = 1024 - len(empty)
    tokens = count(common.serialize_state(state))
    fits = (all(n <= 48 for n in option_tokens) and
            384 - sum(1+n for n in option_tokens) >= max(16, count('choice question: '+request['question'])) and
            tokens <= room)
    if not fits:
        return {'selection': 'unknown', 'available': False, 'error': 'context or question exceeds model budget',
                'state_tokens': tokens, 'state_room': room}
    result = agent.predict(state, {'analysis': question}, max_len=1024, head_max_len=384)
    answer = result['answers']['analysis']
    return {'selection': answer['choice'], 'probabilities': answer['probabilities'],
            'available': True, 'state_tokens': tokens, 'state_room': room,
            'confidence_calibrated': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-path', type=Path, required=True, help='local typed-decisions checkpoint directory')
    args = parser.parse_args()
    request = json.load(sys.stdin)
    os.environ['USE_TF'] = '0'
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['OMP_NUM_THREADS'] = '2'
    os.environ['MKL_NUM_THREADS'] = '2'
    checkpoint = args.model_path.resolve()
    weights = checkpoint / 'model.safetensors'
    digest = hashlib.sha256(weights.read_bytes()).hexdigest()
    if digest != WEIGHTS_SHA256 or checkpoint.name != 'typed-decisions':
        raise ValueError('local checkpoint differs from pinned Laya weights/layout')
    hashes = {str(p.relative_to(checkpoint)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in checkpoint.rglob('*') if p.is_file()}
    import torch
    import laya
    import laya.common as common
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    agent = laya.load(str(checkpoint.parent), subfolder='typed-decisions', device='cpu')
    result = choose(request, agent, common)
    result['model'] = {'name': 'convaiinnovations/laya', 'revision': REVISION,
                       'checkpoint_sha256': hashes, 'laya_version': laya.__version__,
                       'torch_version': torch.__version__, 'device': 'cpu',
                       'worker_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    print(json.dumps(result))


if __name__ == '__main__':
    main()
