from mishe_tauftauf.feed import Feed
from mishe_tauftauf.seed_witness_view import render


def test_witness_reports_discovery_chat_flood(tmp_path, monkeypatch):
    monkeypatch.delenv("MISHE_SEED_SESSION", raising=False)
    feed = Feed(tmp_path)
    for index in range(8):
        feed.append("discover", f"[discovery] scan {index}\nThis scan repeated without a useful change.")
    view = render(tmp_path)
    assert "CHAT RATE: RED — discover: 8 entries" in view
    assert "Trace the wake or sampling feedback loop" in view
