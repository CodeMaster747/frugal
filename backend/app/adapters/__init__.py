"""Ports and adapters for every external boundary (ADR-004).

Each external capability is a ``Protocol`` here, with concrete adapters selected
by configuration and wired at the composition root in ``app/main.py``. Services
depend on the protocol, never the implementation -- which is what lets the whole
test suite run with no network, no credentials, and no Tesseract binary.

Ports land with the milestone that first needs one:

    M4  ObjectStore    S3Store · MinioStore · InMemoryStore
    M4  OCREngine      TesseractEngine · FakeOCREngine
    M7  Forecaster     RecurringProjection · EwmaSeasonal · ProphetForecaster
    M8  PriceProvider  SeedCatalogProvider · ManualEntryProvider · FakePriceProvider
    M10 Notifier       EmailNotifier · NullNotifier

The package exists from M0 so the import-linter contracts covering it are
enforced from the first commit rather than retrofitted once violations exist.
"""
