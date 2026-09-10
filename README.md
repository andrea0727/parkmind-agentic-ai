# ParkMind

**AI-powered multi-agent trip planner for theme parks.**

ParkMind builds a personalized day plan for a theme park visit, monitors conditions in real time (wait times, ride status, weather), and proposes explainable replans always with the guest's approval before anything changes.

Built for the **Agentic AI Engineering** course (MED-42).

## The problem

Guests interact with a theme park in a fragmented way: queuing for rides, checking show times, and checking restaurant availability across separate apps or screens. A plan built at 9 a.m. can be outdated by 10 a.m. if a ride closes, the weather changes, or wait times spike. The real problem isn't generating a plan once — it's keeping it valid.

## Architecture

```
Guest → Orchestrator → Specialized agents (weather, wait times, shows,
        dining, group, profile) → QA evaluator → Synthesizer →
        Proposal → Guest approval → Active plan
                        ↑                              |
                        └── continuous monitoring ──────┘
                            (re-plan on relevant changes)
```

Built with **LangGraph** — its interrupt/resume pattern maps directly onto the guest-approval step, and its checkpointer keeps the plan's state alive for the length of the visit.

## Data sources

| Source | Used for | Status |
|---|---|---|
| [Open-Meteo](https://open-meteo.com) | Weather, air quality | Free, official |
| [ThemeParks.wiki](https://themeparks.wiki) | Wait times, ride status, park hours | Free, community-run (unofficial) |
| Wikimedia | Attraction descriptions / context | Free, official |

Guest profile and group location have no public API and are simulated for this project.

## Scope (v1)

**In scope:** a single park · one-day plan · real-time replanning · human-in-the-loop approval on every change.

**Out of scope for now:** multiple parks, lodging, restaurant reservations, paid skip-the-line queues — considered as future extensions.

## Quality

Groundedness/hallucination checks, business-rule validation, session/memory isolation, and a guest-alignment check (does the recommendation reflect the guest's stated preferences, or just what's operationally convenient for the park). See the [Wiki](../../wiki) for the full test plan.

## Team

Johanna Andrea · Sebastián Toro · Santiago Isaza

## Docs

Full proposal, architecture diagrams, and QA plan live in the [Wiki](../../wiki). 

## Status

🚧 In development — Agentic AI Engineering course project.