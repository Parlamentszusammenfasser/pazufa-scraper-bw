"""Issue #184: a moved tag must not change what runs in jobs that hold deploy secrets."""

import re
from pathlib import Path

WORKFLOWS = Path(__file__).parents[2] / ".github" / "workflows"
_USES = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", re.MULTILINE)


def test_every_action_is_pinned_to_a_commit_sha():
    refs = [ref for wf in WORKFLOWS.glob("*.yml") for ref in _USES.findall(wf.read_text())]

    assert refs
    assert [r for r in refs if not r.startswith("./") and not re.fullmatch(r"[^@]+@[0-9a-f]{40}", r)] == []
