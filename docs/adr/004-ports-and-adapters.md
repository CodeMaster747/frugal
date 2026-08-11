# ADR-004 — Ports and adapters at every external boundary

**Status:** Accepted · **Date:** 2026-08-04

## Context

Frugal depends on several external capabilities that are unstable, expensive, legally constrained, or
simply unavailable in a test environment:

- **Product prices** — the original brief specified scraping Amazon, Flipkart, Croma, and Reliance
  Digital. This violates their terms of service, is defeated by anti-bot defences, and creates real
  liability for a commercial product.
- **OCR** — Tesseract is a system binary that must not be required to run a unit test.
- **Object storage** — S3 needs AWS credentials; local development should need none.
- **Forecasting** — three interchangeable strategies selected at runtime.
- **Notifications** — no-op in development.

## Decision

Define a `Protocol` for each external capability in `app/adapters/ports.py`. Services depend on the
protocol; concrete adapters are selected by configuration and wired at the composition root
(`main.py`).

| Port | v1 adapters | Later |
|---|---|---|
| `PriceProvider` | `SeedCatalogProvider`, `ManualEntryProvider`, `FakePriceProvider` | Affiliate or licensed data API |
| `OCREngine` | `TesseractEngine`, `FakeOCREngine` | Cloud OCR |
| `ObjectStore` | `S3Store`, `MinioStore`, `InMemoryStore` | — |
| `Forecaster` | `RecurringProjection`, `EwmaSeasonal`, `ProphetForecaster` | LightGBM |
| `Notifier` | `EmailNotifier`, `NullNotifier` | Push, webhook |

Every port ships a fake implementation.

## Consequences

**Positive.** The full test suite runs with no network, no credentials, and no Tesseract binary.
Local development uses MinIO and needs no AWS account. Swapping a price source is a config change, not
a refactor. Adapter selection differing only by environment means the code under test is the code that
ships.

**Negative.** One layer of indirection at each boundary, and each port needs a fake maintained
alongside it. Justified where implementations genuinely vary; not applied to stable internal
dependencies.

## Why this matters most for `PriceProvider`

The Purchase Advisor is the flagship feature, and its data source is the single largest external risk
in the project. Wiring a scraper directly into the advisor would make the flagship feature's viability
depend on Amazon's bot defences.

With the port in place, v1 ships a seeded catalogue — fully demonstrable, zero legal exposure, zero
cost — and the advisor's scoring, simulation, and explanation logic is identical to what it will be
with live data. The unresolved question (where prices come from) is isolated from the answered one
(what to do with them).

## Alternative rejected

**Direct integration, refactor when needed.** Faster initially. Rejected because the refactor lands
exactly when it is most expensive: after the advisor is built on top of it, and typically triggered by
an outage or a legal notice rather than by choice.
