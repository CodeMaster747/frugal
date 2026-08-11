-- Creates the test database alongside the development one.
--
-- Tests run against real Postgres rather than SQLite because behaviour must
-- match production: SQLite silently accepts partial indexes, CITEXT, and
-- NUMERIC semantics that Postgres enforces. Running them against a separate
-- database keeps `make test` from destroying local development data.

CREATE DATABASE frugal_test OWNER frugal;
