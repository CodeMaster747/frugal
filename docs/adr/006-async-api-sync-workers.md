# ADR-006 — Async API, synchronous workers, two engines over one schema

**Status:** Accepted · **Date:** 2026-08-04

## Context

Frugal's workload splits cleanly in two:

- **API requests** — I/O-bound. Mostly waiting on Postgres and Redis. High concurrency, short duration.
- **Background jobs** — CPU-bound. OpenCV preprocessing, Tesseract OCR, scikit-learn inference, Prophet
  fitting. Low concurrency, long duration, memory-heavy.

FastAPI supports both async and sync endpoints, and SQLAlchemy 2 supports both async and sync sessions.
Consistency argues for picking one.

## Decision

**API:** async endpoints, `asyncpg` driver, `AsyncSession`.
**Workers:** synchronous Celery tasks, `psycopg` driver, sync `Session`.
**Shared:** one set of declarative models, one migration history, two engines in `core/database.py`.

Celery worker concurrency is **1** on t3.micro.

## Consequences

**Positive.** The API handles concurrent I/O-bound requests efficiently on limited memory. Worker code
stays plain synchronous Python, which is what OpenCV, Tesseract, scikit-learn, and Prophet all expect —
none of them benefit from async, and all of them would block an event loop. The schema is defined once.

**Negative.** Two session types means repositories that serve both paths need sync and async variants,
or duplication. Managed by keeping worker data access in dedicated task-layer functions rather than
sharing the async repositories.

Two drivers means two connection pools against Neon's connection limit. Managed by capping pool sizes
explicitly.

## Why not async everywhere

Wrapping CPU-bound work in `run_in_executor` to satisfy an async API adds a thread-pool layer, obscures
stack traces, and delivers no concurrency benefit — the work is CPU-bound, so it is bounded by cores,
not by waiting. Prophet is not async-aware and never will be. Consistency would cost clarity and buy
nothing.

## Why concurrency 1

Measured worker footprint is roughly 450 MB during a Prophet fit or an OpenCV pipeline on a 5 MP image.
A second concurrent worker on a 1 GB instance is an OOM kill, not a throughput gain. At this tier
throughput comes from queue prioritisation, not parallelism. Concurrency becomes a tuning knob when the
worker moves to its own instance — which is a config change, not a code change.
