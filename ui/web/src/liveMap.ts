import type { Plan, Proposal } from "./api";
import { FIXTURE } from "./fixture";
import { extraLoc } from "./i18n";
import { WALK_M_PER_MIN } from "./geo";
import type { PlanEvent, ProposalView } from "./types";

/* Maps the API contracts (src/parkmind/api/schemas.py) onto the shapes the prototype screens render. */

const hhmm = (iso: string) => iso.slice(11, 16);
const norm = (s: string) => s.trim().toLowerCase();

function locationOf(title: string) {
  const ride = FIXTURE.rides.find((r) => norm(r.name) === norm(title));
  return ride?.loc ?? null;
}

function metaOf(land: string | null, wait: number): string {
  const parts = [land, wait > 0 ? `wait ${wait} min` : null].filter(Boolean);
  return parts.join(" · ");
}

export function planFromApi(plan: Plan): PlanEvent[] {
  return plan.stops.map((s, i) => ({
    event_id: s.stop_id,
    start: hhmm(s.arrival),
    title: s.title,
    meta: metaOf(s.land, s.wait_minutes),
    kind: s.kind,
    state: i === 0 ? "current" : "upcoming",
    location: locationOf(s.title),
    walk_m: s.walking_minutes * WALK_M_PER_MIN,
  }));
}

const signed = (n: number | null | undefined) => (n == null ? null : Math.round(n));

export function proposalFromApi(p: Proposal, current: PlanEvent[]): ProposalView {
  const now = (id: string) => current.find((e) => e.event_id === id);
  const cand = (id: string) => p.plan.stops.find((s) => s.stop_id === id);
  const prov = p.provenance;
  const when = prov.retrieved_at ? ` · ${hhmm(prov.retrieved_at)}` : "";
  const m = p.metrics;

  return {
    proposal_id: p.proposal_id,
    trigger: p.trigger,
    explanation: p.explanation,
    provenance: `snapshot ${prov.snapshot_id}${when} · ${prov.forecast_strategy} · ${prov.optimizer_strategy} · constraints v${prov.constraints_version}`,
    diff: {
      removed: p.diff.removed.map((d) => {
        const e = now(d.stop_id);
        return { ref: d.stop_id, start: e?.start ?? "", title: d.title, meta: e?.meta ?? "" };
      }),
      added: p.diff.added.map((d) => {
        const s = cand(d.stop_id);
        return {
          ref: d.stop_id,
          start: s ? hhmm(s.arrival) : "",
          title: d.title,
          kind: s?.kind ?? "ride",
          location: locationOf(d.title),
          meta: s ? metaOf(s.land, s.wait_minutes) : "",
        };
      }),
      moved: p.diff.moved.map((d) => {
        const s = cand(d.stop_id);
        const e = now(d.stop_id);
        return {
          ref: d.stop_id,
          from: e?.start ?? "",
          to: s ? hhmm(s.arrival) : "",
          title: d.title,
          meta: s ? metaOf(s.land, s.wait_minutes) : "",
        };
      }),
    },
    metrics: {
      unchanged: "",
      walk_delta: "",
      affinity: "",
      unchanged_label: FIXTURE.proposal.metrics.unchanged_label,
      walk_label: FIXTURE.proposal.metrics.walk_label,
      affinity_label: extraLoc("stat_wait"),
      walk_delta_min:
        m.walking_minutes_before == null ? null : signed(m.walking_minutes_after - m.walking_minutes_before),
      wait_delta_min:
        m.wait_minutes_before == null ? null : signed(m.wait_minutes_after - m.wait_minutes_before),
    },
    accessibility_verified: true,
    accessibility_note: extraLoc("a11y_live"),
  };
}

/** After an approved replan the plan is re-read from the API; the diff says what to flag. */
export function markChanged(events: PlanEvent[], view: ProposalView): PlanEvent[] {
  const added = new Set(view.diff.added.map((a) => a.ref));
  const moved = new Set(view.diff.moved.map((m) => m.ref));
  const addedTitles = new Set(view.diff.added.map((a) => norm(a.title)));
  return events.map((e) => {
    if (added.has(e.event_id) || addedTitles.has(norm(e.title))) return { ...e, changed: "tag_new" as const };
    if (moved.has(e.event_id)) return { ...e, changed: "tag_moved" as const };
    return e;
  });
}
