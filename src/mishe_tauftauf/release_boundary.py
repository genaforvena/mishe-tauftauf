"""Only shell text emitted by committed-release promotion."""
import shlex


def wrapper_text(source, python):
    return "#!/bin/sh\nexport PYTHONPATH=" + shlex.quote(str(source/"src")) + "\nexec " + shlex.quote(python) + ' -m mishe_tauftauf "$@"\n'
