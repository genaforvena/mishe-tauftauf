import pytest

from mishe_tauftauf import model_policy
from mishe_tauftauf.engine_claude import ClaudeEngine
from mishe_tauftauf.engine_omp import OmpEngine


@pytest.mark.parametrize("model", ["~anthropic/claude-sonnet-latest", "claude-sonnet-5-5", "Claude-Sonnet-4"])
def test_sonnet_refused_by_both_engines(model):
    with pytest.raises(ValueError, match="banned"):
        ClaudeEngine(model)
    with pytest.raises(ValueError, match="banned"):
        OmpEngine(model)


@pytest.mark.parametrize("model", ["claude-haiku-5-5", "opus", "opencode-go/beam-free", None])
def test_allowed_models_construct(model):
    model_policy.refuse_banned_model(model)
    ClaudeEngine(model or "claude-haiku-5-5")
    OmpEngine(model)
