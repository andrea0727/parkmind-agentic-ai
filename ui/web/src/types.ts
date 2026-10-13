import type { Lang } from "./i18n.data";

export type Loc = string | Partial<Record<Lang, string>>;
export interface LatLon {
  lat: number;
  lon: number;
}

export interface PlanEvent {
  event_id: string;
  start: string;
  title: string;
  meta: Loc;
  kind: string;
  state: string;
  location?: LatLon | null;
  lightning_lane?: boolean;
  changed?: "tag_new" | "tag_moved";
  /** Walking distance into this stop; set by the live adapter, otherwise derived from coordinates. */
  walk_m?: number;
}

export interface ProposalDiff {
  removed: { ref: string; start: string; title: string; meta: Loc }[];
  added: { ref: string; start: string; title: string; kind: string; location?: LatLon | null; meta: Loc }[];
  moved: { ref: string; from: string; to: string; title: string; meta: Loc }[];
}

export interface ProposalView {
  proposal_id: string;
  trigger: Loc;
  explanation: Loc;
  provenance: Loc;
  diff: ProposalDiff;
  metrics: {
    unchanged: string;
    walk_delta: string;
    affinity: string;
    unchanged_label: Loc;
    walk_label: Loc;
    affinity_label: Loc;
    /** Live proposals report minutes directly instead of the prototype's illustrative figures. */
    walk_delta_min?: number | null;
    wait_delta_min?: number | null;
  };
  accessibility_verified: boolean;
  accessibility_note: Loc;
}

export interface Ride {
  kind: string;
  name: string;
  zone: string;
  wait: number | null;
  access: boolean;
  loc: LatLon;
  closed?: boolean;
}

export interface Fixture {
  guest_profile: { guest_id: string; display_name: string; party_size: number };
  party: { name: string; role: string; note: Loc }[];
  party_constraints: { party_size: number; departure_time: string };
  accessibility_requirements: {
    guest_id: string;
    consent: boolean;
    retention_policy: string;
    flags: { code: string; mandatory: boolean; label: Loc }[];
  };
  guest_preferences: { soft: { code: string; weight: number; label: Loc }[] };
  tickets: {
    product: string;
    product_key: string;
    park_hopper: boolean;
    valid_from: string;
    valid_to: string;
    day_index: number;
    day_total: number;
    lightning_lane: { type: string; selections_total: number; selections_used: number };
    holders: { name: string; kind: string }[];
  };
  plan: {
    plan_id: string;
    park: string;
    park_id: string;
    status: string;
    rules_checked: number;
    events: PlanEvent[];
  };
  proposal: ProposalView;
  rides: Ride[];
  alerts: { id: string; kind: string; icon: string; mins: number; title_key: string; body_key: string }[];
}

export interface ChipView {
  kind: "hard" | "soft" | "plain";
  label: string;
}

/** What the assistant says back, whichever source produced it (mock replies or the API). */
export interface Reply {
  text: string;
  constraints?: ChipView[];
  confirm?: boolean;
  consentRequired?: boolean;
  follow?: string;
  plan_preview?: boolean;
  open_proposal?: boolean;
  followups?: boolean;
  eventConfirm?: boolean;
}
