"""A test run must never start inside mishe-lifeline.slice (Genome prevention, 2026-11-02)."""
from tests.conftest import LIFELINE_SLICE, lifeline_refusal

LIFELINE_CGROUP = (
    "0::/user.slice/user-1000.slice/user@1000.service/mishe.slice/"
    "mishe-lifeline.slice/tmux-spawn-021195f2.scope\n"
)
PLANTS_CGROUP = (
    "0::/user.slice/user-1000.slice/user@1000.service/mishe.slice/"
    "mishe-plants.slice/mishe-self-development-current.service\n"
)


def test_lifeline_cgroup_refuses_the_run_with_an_unknown_message():
    message = lifeline_refusal(LIFELINE_CGROUP)
    assert message is not None
    assert message.startswith("UNKNOWN test session started inside " + LIFELINE_SLICE)


def test_plants_and_unslices_cgroups_are_not_refused():
    assert lifeline_refusal(PLANTS_CGROUP) is None
    assert lifeline_refusal("0::/user.slice/user-1000.slice/session-3.scope\n") is None


def test_a_lifeline_name_outside_a_slice_component_is_not_refused():
    # A scope whose name merely contains the slice string is not the slice itself.
    text = "0::/user.slice/mishe-lifeline.slice.notes/x.scope\n"
    assert lifeline_refusal(text) is None


def test_hybrid_or_empty_cgroup_text_is_not_refused():
    assert lifeline_refusal("") is None
    assert lifeline_refusal("1:name=systemd:/mishe-lifeline.slice\n") is None
