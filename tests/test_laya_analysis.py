from types import SimpleNamespace


def test_model_state_retains_excerpts_and_omission_flag_without_source_hash_noise():
    from mishe_tauftauf.laya_analysis import model_state
    value = model_state({'entries': [{'sequence': 3, 'source': 'genome', 'text': 'claim', 'body_sha256': 'abc', 'omitted_characters': 100}], 'context_complete': False, 'previous_completed_analysis': 'prior'})
    assert value['entries'][0] == {'sequence': 3, 'source': 'genome', 'text': 'claim'}
    assert value['context_complete'] is False
    assert value['previous_completed_analysis'] == 'prior'


def test_budget_excess_never_calls_predict():
    from mishe_tauftauf.laya_analysis import choose
    class Tokenizer:
        mask_token = '[MASK]'
        def __call__(self, text, **kwargs):
            return {'input_ids': text.split()}
    class Agent:
        tok = Tokenizer()
        def predict(self, *args, **kwargs):
            raise AssertionError('must not truncate or predict')
    common = SimpleNamespace(serialize_state=lambda s: 'word '*1100,
                             render_options=lambda q: ['short'], build_sequence=lambda *a: (list(range(100)), []))
    result = choose({'question': 'Which analysis?', 'analyses': {'unknown': 'Read evidence'}, 'state': {}}, Agent(), common)
    assert result['selection'] == 'unknown'
    assert result['available'] is False
