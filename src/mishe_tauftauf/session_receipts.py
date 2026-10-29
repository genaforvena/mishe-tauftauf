"""Paths for decoded session identities; secondary sessions preserve the primary."""
import hashlib


def receipt_path(home, session, primary):
    if not isinstance(session, str) or (primary is not None and not isinstance(primary, str)):
        raise ValueError("invalid decoded session identity")
    if primary is None or session == primary:
        return home / ".seed-raised"
    return home / "session-receipts" / hashlib.sha256(session.encode("utf-8")).hexdigest()
