import type { LatLon, PlanEvent, ProposalView } from "./types";

export const HUB = { lat:28.4190, lon:-81.5812 };
export const ZONES: { name: string; fill: string; ink: string; poly: [number, number][] }[] = [
  { name:"Main Street, U.S.A.", fill:"#F7E9D8", ink:"#6B4F2C", poly:[[28.4162,-81.5818],[28.4162,-81.5806],[28.4184,-81.5806],[28.4184,-81.5818]] },
  { name:"Tomorrowland",  fill:"#DDEBF8", ink:"#2C5375", poly:[[28.4183,-81.5795],[28.4182,-81.5766],[28.4200,-81.5763],[28.4204,-81.5783],[28.4194,-81.5796]] },
  { name:"Fantasyland",   fill:"#F3E4F4", ink:"#6B4077", poly:[[28.4196,-81.5800],[28.4199,-81.5784],[28.4213,-81.5786],[28.4214,-81.5807],[28.4203,-81.5811]] },
  { name:"Liberty Square",fill:"#E6EDE3", ink:"#445A3F", poly:[[28.4199,-81.5820],[28.4202,-81.5810],[28.4212,-81.5816],[28.4210,-81.5834],[28.4200,-81.5833]] },
  { name:"Frontierland",  fill:"#F2E2D2", ink:"#6D4A26", poly:[[28.4188,-81.5832],[28.4199,-81.5833],[28.4202,-81.5854],[28.4188,-81.5858],[28.4182,-81.5843]] },
  { name:"Adventureland", fill:"#DFEDDC", ink:"#3A5E35", poly:[[28.4174,-81.5826],[28.4186,-81.5825],[28.4189,-81.5841],[28.4177,-81.5847],[28.4170,-81.5837]] }
];
export const WATER: [number, number][] = [[28.4196,-81.5848],[28.4202,-81.5852],[28.4208,-81.5845],[28.4206,-81.5837],[28.4198,-81.5840]];

/* Red de senderos: Main Street, el anillo del hub, los radios y el bucle
   perimetral que conecta las tierras entre sí. */
export const WALKWAYS: [number, number][][] = (() => {
  const paths: [number, number][][] = [
    [[28.4163,-81.5812],[28.4186,-81.5812]],                 // Main Street
    [[28.4190,-81.5812],[28.4192,-81.5783]],                 // → Tomorrowland
    [[28.4190,-81.5812],[28.4204,-81.5798]],                 // → Fantasyland
    [[28.4190,-81.5812],[28.4203,-81.5824]],                 // → Liberty Square
    [[28.4190,-81.5812],[28.4192,-81.5840]],                 // → Frontierland
    [[28.4190,-81.5812],[28.4182,-81.5833]],                 // → Adventureland
    // bucle perimetral
    [[28.4192,-81.5783],[28.4199,-81.5778]],
    [[28.4199,-81.5778],[28.4205,-81.5789]],
    [[28.4205,-81.5789],[28.4207,-81.5802]],
    [[28.4207,-81.5802],[28.4206,-81.5818]],
    [[28.4206,-81.5818],[28.4205,-81.5831]],
    [[28.4205,-81.5831],[28.4199,-81.5840]],
    [[28.4199,-81.5840],[28.4192,-81.5845]],
    [[28.4192,-81.5845],[28.4185,-81.5842]],
    [[28.4185,-81.5842],[28.4180,-81.5835]],
    [[28.4180,-81.5835],[28.4178,-81.5825]],
    [[28.4178,-81.5825],[28.4180,-81.5815]],
    // ramales internos
    [[28.4199,-81.5778],[28.4196,-81.5773]],
    [[28.4205,-81.5789],[28.4201,-81.5791]],
    [[28.4207,-81.5802],[28.4203,-81.5797]],
    [[28.4205,-81.5831],[28.4202,-81.5841]],
    [[28.4192,-81.5845],[28.4193,-81.5848]],
    [[28.4180,-81.5835],[28.4181,-81.5839]],
    [[28.4186,-81.5812],[28.4180,-81.5815]],
    [[28.4186,-81.5812],[28.4186,-81.5802]],
    [[28.4186,-81.5802],[28.4186,-81.5785]]
  ];
  // anillo alrededor del hub
  const ring: [number, number][][] = [];
  for (let i = 0; i < 12; i++){
    const a = (i/12) * Math.PI * 2, b = ((i+1)/12) * Math.PI * 2, r = 0.00042;
    ring.push([[HUB.lat + r*Math.sin(a), HUB.lon + r*Math.cos(a)*1.14],
               [HUB.lat + r*Math.sin(b), HUB.lon + r*Math.cos(b)*1.14]]);
  }
  return paths.concat(ring);
})();

export const WALK_M_PER_MIN = 72;
export function haversine(a?: LatLon | null, b?: LatLon | null): number | null {
  if (!a || !b) return null;
  const R = 6371000, rad = (d: number) => d*Math.PI/180;
  const dLat = rad(b.lat-a.lat), dLon = rad(b.lon-a.lon);
  const h = Math.sin(dLat/2)**2 + Math.cos(rad(a.lat))*Math.cos(rad(b.lat))*Math.sin(dLon/2)**2;
  return Math.round(2*R*Math.asin(Math.sqrt(h)));
}
export const walkMin = (m: number | null | undefined): number | null => m == null ? null : Math.max(1, Math.round(m/WALK_M_PER_MIN));

export function project(points: LatLon[], w: number, h: number, pad: number) {
  const lats = points.map(p=>p.lat), lons = points.map(p=>p.lon);
  const latC = (Math.min(...lats)+Math.max(...lats))/2, kx = Math.cos(latC*Math.PI/180);
  const xs = lons.map(l=>l*kx), ys = lats.map(l=>-l);
  const x0=Math.min(...xs), x1=Math.max(...xs), y0=Math.min(...ys), y1=Math.max(...ys);
  const s = Math.min((w-2*pad)/((x1-x0)||1), (h-2*pad)/((y1-y0)||1));
  return (p: LatLon) => ({
    x: pad + (p.lon*kx - x0)*s + (w-2*pad-(x1-x0)*s)/2,
    y: pad + (-p.lat  - y0)*s + (h-2*pad-(y1-y0)*s)/2
  });
}

export const MAP_W = 360;
export const MAP_H = 430;

export function legMeters(prev: PlanEvent | undefined, ev: PlanEvent): number | null {
  if (ev.walk_m != null) return Math.round(ev.walk_m);
  if (!prev) return null;
  return haversine(prev.location, ev.location);
}

export function totalWalk(events: PlanEvent[]): number {
  return events.reduce((sum, ev, i) => sum + (legMeters(events[i - 1], ev) || 0), 0);
}

/* One function previews the metrics and applies the change, so the sheet and the plan cannot diverge. */
export function applyDiff(events: PlanEvent[], p: ProposalView): PlanEvent[] {
  const rm = p.diff.removed.map((x) => x.ref);
  const out: PlanEvent[] = events.filter((e) => !rm.includes(e.event_id)).map((e) => ({ ...e }));
  p.diff.added.forEach((a) =>
    out.push({
      event_id: a.ref,
      start: a.start,
      title: a.title,
      meta: a.meta,
      kind: a.kind,
      state: "upcoming",
      location: a.location,
      changed: "tag_new",
    }),
  );
  p.diff.moved.forEach((mv) => {
    const e = out.find((x) => x.event_id === mv.ref);
    if (e) {
      e.start = mv.to;
      e.meta = mv.meta;
      e.changed = "tag_moved";
    }
  });
  out.sort((a, b) => a.start.localeCompare(b.start));
  return out;
}

export function allPointsFor(rides: { loc?: LatLon | null }[]): LatLon[] {
  const pts: LatLon[] = [];
  ZONES.forEach((z) => z.poly.forEach(([lat, lon]) => pts.push({ lat, lon })));
  WATER.forEach(([lat, lon]) => pts.push({ lat, lon }));
  rides.forEach((r) => r.loc && pts.push(r.loc));
  return pts;
}
