# Evaluation Methodology

The code lives in `src/parkmind/services/evaluation/`. This file is the
write-up: what each metric means, how to interpret it, and how the
baselines are run.

## Headline metrics (built this month)

**Preference Alignment Score** — how closely a generated plan matches a
guest's stated preferences. `services/evaluation/metrics/personalization.py`.

**Personalization Lift** — Jaccard distance between the itineraries
generated for two opposite profiles (e.g. low-intensity family vs.
maximizer adult) on the same day/park/constraints. This is the number that
proves the core thesis: if it's near zero, the system isn't actually
personalizing, no matter what the demo narrative claims.

## Baselines

1. **Popular-first** — no personalization, no optimization.
2. **Static planner** — real optimizer, run once, never replans.
3. **LLM-only** — general-purpose LLM + tools, no guest model, no
   deterministic core, no constraint gate. Run manually 2-3 times for the
   demo narrative rather than as an automated pipeline this month.

## Sanity-check metrics (not the headline evidence)

Hard constraint violation rate, accessibility violation rate, total wait
minutes, total walking minutes. A personalized plan that's operationally
terrible is still a bad plan — but these aren't what to lead with in the
defense.
