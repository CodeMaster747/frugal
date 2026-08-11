# ADR-001 — Modular monolith with tool-enforced boundaries

**Status:** Accepted · **Date:** 2026-08-04

## Context

Frugal spans twelve domain modules. The original brief called for a "microservice-ready" architecture.
Development is by one person on free-tier infrastructure with 1 GB of application RAM.

"Microservice-ready" is frequently interpreted as "build microservices now," which at this scale means
paying distributed-systems costs — network failure modes, distributed tracing, per-service deploys,
cross-service transactions — before there is any load to justify them.

## Decision

Build a **modular monolith**: one FastAPI application, one deployable, one migration history, one test
suite. Internal structure is strictly modular — `app/modules/<domain>/`, each owning its models,
schemas, repository, service, and router.

The boundary rule:

- A module may import `app.core.*`, `app.adapters.*` (ports), and another module's **`service.py`**.
- A module may **never** import another module's `models.py`, `repository.py`, or internal helpers.
- No cross-module ORM relationship or JOIN.

Enforced by an `import-linter` contract in CI. Violations fail the build.

## Consequences

**Positive.** One process to deploy, debug, and monitor. Refactoring across modules is a single atomic
commit. Local development is `docker compose up`. Fits the memory budget.

**Negative.** Nothing prevents a lazy cross-module call from becoming a hidden coupling *through the
service layer* — the linter catches internal imports, not excessive service chatter. Requires
discipline about what belongs in a service interface.

**Extraction path.** Because callers depend on a service interface returning DTOs rather than on ORM
objects, extracting a module means replacing a local service call with an HTTP client behind the same
signature. Forecasting and receipts are the natural first candidates — both are CPU-bound and
independently scalable.

## Why the linter, specifically

A boundary rule maintained by code review decays; every "just this once" import is individually
reasonable and collectively fatal. A rule maintained by a failing build does not decay. The cost is one
config file; the benefit is that the extraction path stays real rather than aspirational.

## Alternatives rejected

**Microservices from day one** — distributed complexity with no scale to justify it, and 1 GB cannot
host multiple services plus their infrastructure.

**Unstructured monolith** — fastest initially, but with twelve modules and cross-cutting engines it
becomes a mesh of interdependencies within months, and the extraction path is then a rewrite.
