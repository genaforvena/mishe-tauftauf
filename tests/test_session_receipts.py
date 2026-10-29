from mishe_tauftauf.session_receipts import receipt_path


def test_secondary_session_cannot_overwrite_primary_receipt(tmp_path):
    primary = receipt_path(tmp_path, "normal", "normal")
    secondary = receipt_path(tmp_path, "life-support", "normal")
    assert primary == tmp_path / ".seed-raised"
    assert primary != secondary and secondary.is_relative_to(tmp_path)


def test_default_preserves_existing_single_session_contract(tmp_path):
    assert receipt_path(tmp_path, "normal", None) == tmp_path / ".seed-raised"


def test_session_identity_never_becomes_a_filesystem_path(tmp_path):
    path = receipt_path(tmp_path, "../../elsewhere", "normal")
    assert path.parent == tmp_path / "session-receipts"
