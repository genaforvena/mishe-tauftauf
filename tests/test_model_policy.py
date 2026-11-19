import pytest

from mishe_tauftauf import model_policy
from mishe_tauftauf.engine_claude import ClaudeEngine
from mishe_tauftauf.engine_omp import OmpEngine


@pytest.mark.parametrize("model", ["~anthropic/claude-sonnet-latest", "claude-sonnet-5-5", "claude-haiku-5-5", "opus", "opencode-go/beam-free", None])
def test_models_construct_no_ban(model):
    model_policy.refuse_banned_model(model)
    ClaudeEngine(model or "claude-haiku-5-5")
    OmpEngine(model)


def test_ban_mechanism_still_refuses_a_marked_model(monkeypatch):
    monkeypatch.setattr(model_policy, "BANNED_MODEL_MARKERS", ("sonnet",))
    with pytest.raises(ValueError, match="banned"):
        ClaudeEngine("claude-sonnet-5-5")
    with pytest.raises(ValueError, match="banned"):
        OmpEngine("~anthropic/claude-sonnet-latest")
