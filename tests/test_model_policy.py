import pytest

from mishe_tauftauf import model_policy
from mishe_tauftauf.engine_claude import ClaudeEngine
from mishe_tauftauf.engine_omp import OmpEngine
from mishe_tauftauf.engine_codex import CodexEngine
from mishe_tauftauf.engine_qwen import QwenEngine


@pytest.mark.parametrize("engine", [ClaudeEngine, OmpEngine, CodexEngine, QwenEngine])
@pytest.mark.parametrize("model", ["opencode-go/glm-5.3", "routerai/z-ai/GLM-5.3-flash"])
def test_glm_53_is_refused_at_every_engine_boundary(engine, model):
    with pytest.raises(ValueError, match="banned"):
        engine(model)


@pytest.mark.parametrize("model", ["~anthropic/claude-sonnet-latest", "claude-sonnet-5-5", "claude-haiku-5-5", "opus", "opencode-go/beam-free"])
def test_models_construct_no_ban(model):
    model_policy.refuse_banned_model(model)
    ClaudeEngine(model or "claude-haiku-5-5")
    OmpEngine(model)


@pytest.mark.parametrize("engine", [ClaudeEngine, OmpEngine, CodexEngine, QwenEngine])
@pytest.mark.parametrize("model", [None, ""])
def test_blank_model_is_refused_at_every_engine_boundary(engine, model):
    with pytest.raises(ValueError, match="empty"):
        engine(model)


@pytest.mark.parametrize("model", [None, ""])
def test_blank_model_is_refused_by_policy(model):
    with pytest.raises(ValueError, match="empty"):
        model_policy.refuse_banned_model(model)


def test_ban_mechanism_still_refuses_a_marked_model(monkeypatch):
    monkeypatch.setattr(model_policy, "BANNED_MODEL_MARKERS", ("sonnet",))
    with pytest.raises(ValueError, match="banned"):
        ClaudeEngine("claude-sonnet-5-5")
    with pytest.raises(ValueError, match="banned"):
        OmpEngine("~anthropic/claude-sonnet-latest")
