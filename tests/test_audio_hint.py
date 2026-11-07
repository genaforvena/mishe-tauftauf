"""Pure hint matching for tools/audio_hint.py (no audio, no devices, no site data)."""
import importlib.util
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def load():
    spec = importlib.util.spec_from_file_location("audio_hint", TOOLS / "audio_hint.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["audio_hint"] = mod
    spec.loader.exec_module(mod)
    return mod


VOCAB_RAW = {
    "hint_words": ["mishe", "миша", "мише", "мишка"],
    "stop_words": ["стоп", "stop"],
    "stop_phrases": ["хватит слушать", "stop listening"],
}


def vocab(ah):
    v = {
        "prompt": "x",
        "hint_words": {ah.tok(w) for w in VOCAB_RAW["hint_words"]},
        "stop_words": [ah.tok(w) for w in VOCAB_RAW["stop_words"]],
        "stop_phrases": [tuple(ah.tokens(p)) for p in VOCAB_RAW["stop_phrases"]],
    }
    return v


def test_hint_word_exact_only_not_fuzz():
    ah = load()
    v = vocab(ah)
    assert ah.find_hint("Миша, включи свет", v)[1] == 0
    assert ah.find_hint("Мишка слушай", v) is not None
    # one-letter fuzz is rejected for hint words (the Маша case)
    assert ah.find_hint("Маша, включи свет", v) is None


def test_hint_returns_text_from_hint_word():
    ah = load()
    v = vocab(ah)
    idx, offset, toks = ah.find_hint("привет, Мише, как дела", v)
    assert "привет, Мише, как дела"[offset:].startswith("Мише")


def test_stop_phrases_exact_and_stop_word_fuzz_first_word_only():
    ah = load()
    v = vocab(ah)
    assert ah.is_stop(["хватит", "слушать"], v)
    assert ah.is_stop(["stop", "listening"], v)
    assert ah.is_stop(["стоп"], v)
    assert ah.is_stop(["стоб"], v)  # one letter of fuzz on a stop word
    assert not ah.is_stop(["с", "тобой"], v)  # 'с тобой' is not a stop
    assert not ah.is_stop(["хватит"], v)  # half of a phrase is not a stop
    assert not ah.is_stop(["расскажи", "стоп"], v)  # stop word must lead


def test_edit_distance():
    ah = load()
    assert ah.edit_distance("стоп", "стоб") == 1
    assert ah.edit_distance("стоп", "стоп") == 0
    assert ah.edit_distance("стоп", "с тобой") > 1


def test_vocabulary_loader_fails_closed(tmp_path):
    ah = load()
    d = tmp_path / "spatial" / "work" / "household"
    d.mkdir(parents=True)
    try:
        ah.load_vocabulary(tmp_path)
        raise AssertionError("missing vocabulary must fail")
    except OSError:
        pass
    (d / "hint_vocabulary.json").write_text('{"prompt": "x"}')
    try:
        ah.load_vocabulary(tmp_path)
        raise AssertionError("partial vocabulary must fail")
    except ValueError:
        pass


def test_transcribe_refuses_auto_language():
    # base on real English collapsed to Chinese under -l auto (98.9% error): language must be pinned.
    import pytest
    with pytest.raises(ValueError):
        load().transcribe(Path("unused.wav"), "prompt", "auto")


def test_prompt_file_overrides_json_prompt(tmp_path):
    ah = load()
    d = tmp_path / "spatial" / "work" / "household"
    d.mkdir(parents=True)
    full = {
        "prompt": "old json prompt",
        "hint_words": ["mishe"],
        "stop_words": ["stop"],
        "stop_phrases": ["stop listening"],
    }
    import json as _json
    (d / "hint_vocabulary.json").write_text(_json.dumps(full))
    assert ah.load_vocabulary(tmp_path)["prompt"] == "old json prompt"
    (d / "whisper-vocab.txt").write_text("Розалия, Rozalia, Роза\n")
    assert ah.load_vocabulary(tmp_path)["prompt"] == "Розалия, Rozalia, Роза"
    (d / "whisper-vocab.txt").write_text("\n")
    try:
        ah.load_vocabulary(tmp_path)
        raise AssertionError("empty prompt file must fail closed")
    except ValueError:
        pass
