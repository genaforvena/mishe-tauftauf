"""Versioned questions: verdicts concern violations, not writing fluency."""
VERSION = 1
POST = [
('R01', 'Can the reader understand what happened without opening an artifact?'),
('R02', 'Does the message explain why this event matters?'),
('R03', 'Is evidence explained by meaning rather than opaque hashes alone?'),
('R04', 'Is the next action/owner or terminal outcome clear when applicable?'),
('R05', 'Does the message distinguish progress from unchanged checks or prerequisites?'),
('R06', 'Is the prose consistent with the full structured event?'),
('R07', 'Does the message preserve known task identity or explain a rename?'),
('R08', 'Does this add meaningful evidence rather than repeat an unchanged notice?')]
PITFALLS = [
('P01','selection','Is another active attempt already claiming this task?'),
('P02','selection','Is this task or its required outcome already terminal?'),
('P03','selection','Does every named prerequisite resolve to durable evidence?'),
('P04','selection','Has the explicit retry fired since the consumed attempt?'),
('P05','both','Do dependencies form a cycle?'),
('P06','both','Is an awaited producer already complete and awaiting reconciliation?'),
('P07','selection','Does the ready step actually require a missing prerequisite first?'),
('P08','selection','Does this repeat an action/blocker without new relevant evidence?'),
('P09','selection','Does this action advance a different task under this identity?'),
('P10','selection','Is there an admissible useful step while final acceptance waits?'),
('P11','selection','Is the mind waiting for an owned deliverable it should produce?'),
('P12','selection','Does a gate require an action result before permitting that action?'),
('P13','selection','Are mutation resources owned and unrelated work preserved?'),
('P14','selection','Is a role treated as an unnecessary repair restriction?'),
('P15','selection','Is a ready operator obligation repeatedly deferred without reason?'),
('P16','handoff','Do evidence files/sequences match claimed versions?'),
('P17','handoff','What relevant deliverable changed beyond logs or paraphrase?'),
('P18','handoff','Does the result label accurately describe checked effects?'),
('P19','handoff','Does success concern the exact candidate/source/runtime?'),
('P20','handoff','Is advice, dispatch, review or self-test mistaken for live outcome?'),
('P21','handoff','Are acceptance checks/open children satisfied before closure?'),
('P22','handoff','Is the next step executable now or an explicit producer/retry wait?'),
('P23','handoff','Does the handoff preserve identity and reconcile prior effects?'),
('P24','handoff','Was the cause merely routed or relabeled without repair?'),
('P25','handoff','Is this outside capability missing or recoverable local preparation?'),
('P26','both','Is analysis repeating while other useful work is available?'),
('P27','handoff','Was the demonstrated cause repaired and acceptance rerun?'),
('P28','handoff','Is this linked to an existing cause finding instead of duplicate work?')]


def questions(stage):
    if stage not in {'post', 'selection', 'handoff'}:
        raise ValueError('unsupported publication check stage')
    pairs = POST if stage == 'post' else [(i,t) for i,s,t in PITFALLS if s in {stage,'both'}]
    return [{'id': i, 'question': t, 'group': 'readability' if i.startswith('R') else 'pitfalls'} for i,t in pairs]
