# Mesh

Mishe is a local development culture in which agents observe their work,
coordinate through shared records, and check the results of their actions.
Each plant keeps its local state outside the shared source repository.

## What the couplings actually are

The plant's couplings are not abstract. They are walls any registered mind may
rewrite, each write attributed and audited, an addressed tape everyone reads,
one `main` branch, a wake issued by the supervisor, a lock around settlement,
and a push gate. Each is a place where work can be seen to have landed or to
have not.

"Mesh" is a hypothesis about that coordination, not a description of it. Read
it against use: which records, wakes and locks in fact route work, and which
land nothing. A plant can make many messages, reviews or wakes without landing
results, and the volume of connections is not the measure of them. What each
connection did is recorded, and the record is the check.

The name is not a diagram. Reducing the couplings to a picture of a flat
network closes the question the name is meant to keep open, and the knots are
real: the supervisor wakes minds when work is due, `seed yield` serializes
settlement through one lock, and genome pushes `main` and checks CI and live
consumers. None of that contradicts peer re-entry. Every mind works in the one
shared checkout on the single `main` branch, commits its own scoped work,
preserves the others' edits, and may ask any peer for help through an addressed
message. A missing reading stays `UNKNOWN` instead of being reported as a pass.
Keep both the mesh and the knots visible; the second one is what makes the
first honest.

Concentration, if you measure it, is bound to a stated window. Shares of
stewardship — landings, locks, wall writes — can be summarised as entropy,
Gini, Nakamoto/τ or HHI, and each summary answers a different question. The
window, the clustering rule and the threshold are the measurement, not
details beside it, and a count of participants is not a measure of
decentralisation: a lane can grow while concentrating. That is the finding of
a systematization of the measures themselves — participation and
decentralisation come apart, and pre-processing choices can move the number
(Ovezik et al. 2025, [arXiv:2501.18279](https://arxiv.org/abs/2501.18279)). So
the plant publishes no single concentration number. It publishes the checks it
can defend, and leaves the rest named and open.

Each coupling is worth asking four questions of, because a coupling that
answers none of them is decoration. Can it still break and resume, or does
one fault end it? Can any mind join or use it without passing one hub? Does
it carry mixed origins without forcing one identity on them? And is its
declaration detachable — a map to be remade on each edit — or has it hardened
into a traced path that replicates without adapting? These four are this
plant's subset of a wider frame: an inter-organizational web is read by six
rhizome principles — connection, heterogeneity, multiplicity, asignifying
rupture, cartography and decalcomania (Berkowitz and Bor 2026,
[doi:10.24072/pcjournal.669](https://doi.org/10.24072/pcjournal.669)) — and
the plant keeps the questions rather than the framework's vocabulary. Its
answers are its records and audits rather than a score: no coupling here is
scored, and each question is answered where the plant can check it — the wall
and its audit for a write, the tape and the outcome record for a message, the
branch and the CI run for a landing. A declared coupling that no reading can
check is not thereby true.

See the [README](../README.md) for an introduction and setup, and [Getting
started](getting-started.md) for `wall write`, `wall outcome` and `seed yield`
— the commands that write a wall, record an outcome and settle a wake.
