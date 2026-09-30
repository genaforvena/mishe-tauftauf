from mishe_tauftauf.laya_review import check_budget, run_questions

class Tokenizer:
    mask_token = '<mask>'
    def __call__(self, text, **kwargs):
        return {'input_ids': list(range(len(text.split())))}
class Common:
    @staticmethod
    def render_options(question): return question['crit']
    @staticmethod
    def build_sequence(tok, state, question, max_len, head_max_len): return list(range(200)), None
    @staticmethod
    def serialize_state(state): return str(state)
class Agent:
    tok = Tokenizer()
    calls = 0
    def predict(self, state, questions, **kwargs):
        self.calls += 1
        return {'answers': {'review': {'choice':'unknown', 'probabilities': {}}}}


def test_oversize_episode_unknown_without_inference():
    agent = Agent()
    request = {'input_hash':'abc','body':'word '*2000,'context':{},'questions':[{'id':'R01','question':'Explain?'}]}
    report = run_questions(request, agent, Common)
    assert report['results'][0]['verdict'] == 'unknown'
    assert agent.calls == 0


def test_incomplete_episode_is_not_clear():
    agent = Agent()
    request = {'input_hash':'abc','body':'A useful event','context':{},'questions':[{'id':'R01','question':'Explain?'}]}
    report = run_questions(request, agent, Common)
    assert report['results'][0]['verdict'] == 'unknown'
    assert agent.calls == 0
