import { useEffect, useRef } from "react";
import { useApp } from "../ctx";
import { FIXTURE } from "../fixture";
import { HUB, MAP_H, MAP_W, WALKWAYS, WATER, ZONES, allPointsFor, project } from "../geo";
import type { Filter } from "../controller";
import type { PlanEvent, Ride } from "../types";

const ALL = allPointsFor(FIXTURE.rides);
const PT_BIG = project(ALL, MAP_W, MAP_H, 16);
const PT_MINI = project(ALL, 350, 210, 12);
type Pt = ReturnType<typeof project>;

const fx = (n: number) => n.toFixed(1);

export function visibleRides(plan: PlanEvent[], filter: Filter): Ride[] {
  const inPlan = new Set(plan.map((e) => e.title));
  return FIXTURE.rides.filter((r) => {
    if (filter === "plan") return inPlan.has(r.name);
    if (filter === "access") return r.access;
    if (filter === "open") return !r.closed;
    return true;
  });
}

/* Layer order: land fill, water, walkways, labels, castle. Labels sit last so walkways never cover them. */
function MapBase({ pt, labels, thin }: { pt: Pt; labels: boolean; thin: boolean }) {
  const path = (poly: [number, number][]) =>
    poly
      .map(([lat, lon], i) => {
        const p = pt({ lat, lon });
        return `${i ? "L" : "M"}${fx(p.x)} ${fx(p.y)}`;
      })
      .join(" ") + " Z";
  const hub = pt(HUB);
  return (
    <>
      {ZONES.map((z) => (
        <path key={z.name} d={path(z.poly)} fill={z.fill} stroke="#fff" strokeWidth={2} />
      ))}
      <path d={path(WATER)} fill="#CFE6F2" stroke="#B9DAEA" strokeWidth={1.5} />
      {WALKWAYS.map(([a, b], i) => {
        const p1 = pt({ lat: a[0], lon: a[1] });
        const p2 = pt({ lat: b[0], lon: b[1] });
        return (
          <line key={i} x1={fx(p1.x)} y1={fx(p1.y)} x2={fx(p2.x)} y2={fx(p2.y)} stroke="#fff"
            strokeWidth={thin ? 2.5 : 3.4} strokeLinecap="round" opacity={0.85} />
        );
      })}
      {labels &&
        ZONES.map((z) => {
          const c = z.poly.reduce((a, [lat, lon]) => ({ lat: a.lat + lat / z.poly.length, lon: a.lon + lon / z.poly.length }), { lat: 0, lon: 0 });
          const cp = pt(c);
          return (
            <text key={z.name} x={cp.x} y={cp.y} textAnchor="middle" fontSize={9.5} fontWeight={800} fill={z.ink}
              stroke={z.fill} strokeWidth={3.2} paintOrder="stroke" strokeLinejoin="round" style={{ pointerEvents: "none" }}>
              {z.name}
            </text>
          );
        })}
      <g style={{ pointerEvents: "none" }}>
        <circle cx={hub.x} cy={hub.y} r={12} fill="#fff" stroke="#E7E3F4" strokeWidth={2} />
        <text x={hub.x} y={hub.y + 4} textAnchor="middle" fontSize={11}>🏰</text>
      </g>
    </>
  );
}

export function PlanMap({ plan, label }: { plan: PlanEvent[]; label: string }) {
  const stops = plan.filter((e) => e.location);
  const W = 350, H = 210;
  if (!stops.length) return <svg className="planmap" role="img" aria-label={label} viewBox={`0 0 ${W} ${H}`} />;
  const route = stops.map((s, i) => { const p = PT_MINI(s.location!); return `${i ? "L" : "M"}${p.x} ${p.y}`; }).join(" ");
  return (
    <svg className="planmap" role="img" aria-label={label} viewBox={`0 0 ${W} ${H}`}>
      <rect width={W} height={H} fill="#EEF4E8" />
      <MapBase pt={PT_MINI} labels={false} thin />
      <path d={route} fill="none" stroke="#4A2FA8" strokeWidth={2.5} strokeDasharray="6 5" strokeLinecap="round" opacity={0.8} />
      {stops.map((s, i) => {
        const p = PT_MINI(s.location!);
        const fill = s.changed ? "#12938C" : s.state === "current" ? "#FFB627" : "#4A2FA8";
        return (
          <g key={s.event_id}>
            <circle cx={p.x} cy={p.y} r={9.5} fill={fill} stroke="#fff" strokeWidth={2} />
            <text x={p.x} y={p.y + 3.4} textAnchor="middle" fontSize={9.5} fontWeight={800} fill="#fff">{i + 1}</text>
          </g>
        );
      })}
    </svg>
  );
}

export function BigMap() {
  const { c, t } = useApp();
  const { plan, map } = c.s;
  const svgRef = useRef<SVGSVGElement>(null);
  const setMap = c.actions.setMap;
  const mapRef = useRef(map);
  mapRef.current = map;
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const drag = useRef<{ x: number; y: number; cx: number; cy: number; active: boolean } | null>(null);
  const pinch = useRef<{ d: number; zoom: number } | null>(null);
  const suppressClick = useRef(false);

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      setMap({ zoom: mapRef.current.zoom * (e.deltaY < 0 ? 1.12 : 0.89) });
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, [setMap]);

  const onDown = (e: React.PointerEvent<SVGSVGElement>) => {
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.current.size === 1) drag.current = { x: e.clientX, y: e.clientY, cx: map.cx, cy: map.cy, active: false };
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      pinch.current = { d: Math.hypot(a.x - b.x, a.y - b.y), zoom: map.zoom };
      drag.current = null;
    }
  };
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!pointers.current.has(e.pointerId)) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pinch.current && pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      suppressClick.current = true;
      setMap({ zoom: pinch.current.zoom * (Math.hypot(a.x - b.x, a.y - b.y) / pinch.current.d) });
      return;
    }
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x, dy = e.clientY - d.y;
    /* Capture only once it is a real drag, so a plain tap still reaches the pin underneath. */
    if (!d.active && Math.hypot(dx, dy) > 4) {
      d.active = true;
      suppressClick.current = true;
      e.currentTarget.setPointerCapture(e.pointerId);
    }
    if (d.active) {
      const r = e.currentTarget.getBoundingClientRect();
      setMap({ cx: d.cx - dx / (r.width * mapRef.current.zoom), cy: d.cy - dy / (r.height * mapRef.current.zoom) });
    }
  };
  const onEnd = (e: React.PointerEvent<SVGSVGElement>) => {
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) pinch.current = null;
    if (!pointers.current.size) drag.current = null;
    if (suppressClick.current) setTimeout(() => { suppressClick.current = false; }, 0);
  };

  const stops = plan.filter((e) => e.location);
  const visible = visibleRides(plan, map.filter).filter((r) => r.loc);
  const route = stops.map((s, i) => { const p = PT_BIG(s.location!); return `${i ? "L" : "M"}${p.x} ${p.y}`; }).join(" ");
  const meLoc = (plan.find((e) => e.state === "current") ?? plan[0])?.location ?? HUB;
  const me = PT_BIG(meLoc);
  const w = MAP_W / map.zoom, h = MAP_H / map.zoom;
  const viewBox = `${map.cx * MAP_W - w / 2} ${map.cy * MAP_H - h / 2} ${w} ${h}`;

  const pick = (name: string) => {
    if (suppressClick.current) return;
    setMap({ selected: name });
    setTimeout(() => document.getElementById("rcClose")?.focus(), 0);
  };

  return (
    <svg id="bigMap" ref={svgRef} role="img" aria-label={t("a_map")} viewBox={viewBox}
      onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onEnd} onPointerCancel={onEnd}>
      <rect x={-500} y={-500} width={1500} height={1600} fill="#EEF4E8" />
      <MapBase pt={PT_BIG} labels thin={false} />
      <path d={route} fill="none" stroke="#4A2FA8" strokeWidth={3} strokeDasharray="7 6" strokeLinecap="round" opacity={0.75} />
      {visible.map((r) => {
        const p = PT_BIG(r.loc);
        const idx = stops.findIndex((s) => s.title === r.name);
        const sel = map.selected === r.name;
        let fill = "#9B95B5", label = "";
        if (r.closed) fill = "#E0218A";
        else if (idx >= 0) { fill = "#4A2FA8"; label = String(idx + 1); }
        else if (!r.access) fill = "#C9C2E2";
        else if ((r.wait ?? 0) >= 60) fill = "#FFB627";
        else fill = "#12938C";
        const rad = idx >= 0 ? 10 : 6.5;
        const aria = `${r.name}, ${r.zone}, ${r.closed ? t("closed_now") : `${r.wait} ${t("min_wait")}`}`;
        return (
          <g key={r.name} className="pin" tabIndex={0} role="button" aria-label={aria} style={{ cursor: "pointer" }}
            onClick={() => pick(r.name)}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(r.name); } }}>
            {sel && <circle cx={p.x} cy={p.y} r={rad + 7} fill={fill} opacity={0.22} />}
            <circle cx={p.x} cy={p.y} r={rad} fill={fill} stroke="#fff" strokeWidth={2.4} />
            {label && <text x={p.x} y={p.y + 3.4} textAnchor="middle" fontSize={9.5} fontWeight={800} fill="#fff" style={{ pointerEvents: "none" }}>{label}</text>}
            {r.closed && <text x={p.x} y={p.y + 3} textAnchor="middle" fontSize={8.5} fontWeight={800} fill="#fff" style={{ pointerEvents: "none" }}>✕</text>}
          </g>
        );
      })}
      <g style={{ pointerEvents: "none" }}>
        <circle cx={me.x} cy={me.y} r={15} fill="#4A2FA8" opacity={0.15} />
        <circle cx={me.x} cy={me.y} r={5} fill="#4A2FA8" stroke="#fff" strokeWidth={2.4} />
      </g>
    </svg>
  );
}
