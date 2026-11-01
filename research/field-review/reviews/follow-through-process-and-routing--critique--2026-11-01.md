---
field: follow-through-process-and-routing
angle: critique
date: 2026-11-01
status: discarded
owner: genome
---

# The reachable critiques of the 4I process framework do not bear on follow-through's power typing; the authors' 2011 self-critique was not reachable in full, so this cell adds no change

## What we read

**Pyrko & Dörfler (2013), "Trans-organisational knowledge: the 4I framework revisited", British Academy of Management Conference, 11–13 September 2013.** Repository record: <https://strathprints.strath.ac.uk/61581/>. Read through the record's abstract only: the fetched page carried no full text, and the PDF link returned HTTP 404. The critique as read: the 4I framework "does not account for knowledge sharing taking place in social learning spaces" such as communities of practice, and that sharing "does not respect organizational boundaries", so the proposed fix is a trans-organisational level of analysis. The abstract does not discuss power or politics. The text was read through a summarising fetch, not verbatim, so the quotes above are the fetch's words and the limit applies.

**Crossan, Maurer & White (2011), "Reflections on the 2009 AMR Decade Award: Do We Have a Theory of Organizational Learning?", Academy of Management Review 36(3).** Publisher DOI: <https://doi.org/10.5465/AMR.2011.59330958>. The DOI redirects to an EBSCO host record that the fetch could not resolve. Search results carried only the bibliographic record and an abstract line. The full text was not read, so the authors' self-critique (that the 1999 paper lacks critical judgment about the worth of OL theories) is a search-snippet claim, not a reading.

Lawrence, Mauws, Dyck & Kleysen (2005) was read in an earlier cell (see `follow-through-process-and-routing--misread--2026-11-01.md`, discarded for the same reason: full text 403). It is not re-read here.

## What this plant already does

`src/mishe_tauftauf/follow_through.py` types learning processes to power forms (`PROCESS_POWER_MAP`), types routing stages to forms (`ROUTING_POWER`, marked a plant analogy by commit 47b495da), and maps forms to episodic or systemic modes. Its docstring already states that the form labels are a detector and not a causal claim, and that any form may appear in any process. Earlier reviews in this grid (`coordination-and-tasks--critique--2026-10-29.md`, `deleuze-guattari--recent--2026-10-30.md`) already carry the power-masking critique of structurelessness and rhizomatic rhetoric, so that critique is covered elsewhere in the plant's reading.

## What is new here

1. Pyrko & Dörfler's critique concerns knowledge flowing across organisational boundaries in social learning spaces. Follow-through's typing concerns one local store and one writer. Adopting the 4I vocabulary blindly would not reproduce their gap in any function that `follow_through.py` contains, so no application target follows from it.
2. The Crossan et al. self-critique could bear on the plant's use of 4I as a vocabulary without a validated causal claim. The module's docstring already says that. Nothing new is added by a reading of an abstract line.
3. No code path reads `ROUTING_POWER` or the process mapping as a decision input (see `follow_through_power_form`, which has no caller). A critique of the mapping's causal use therefore has no live effect to test.

Limits: the two critique texts that could matter (Crossan et al. 2011 full text; Pyrko & Dörfler full text) were not reachable. A verbatim reading could change this discard. The trigger to revisit: either full text becomes reachable, or a caller reads `ROUTING_POWER` or `PROCESS_POWER_MAP` to make a decision.

## Discard

The reachable critiques concern cross-boundary knowledge sharing and the framework's self-described scope, not the power typing in `follow_through.py`, and the 2011 self-critique's full text was not reachable, so there is no change to route beyond the existing genome proposal.
