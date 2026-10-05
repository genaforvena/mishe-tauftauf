from types import SimpleNamespace



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
