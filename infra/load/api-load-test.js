// Load test for the Frugal API — NFR-1.
//
//   k6 run -e BASE_URL=http://localhost:8000 api-load-test.js
//
// Asserts the two budgets separately, because they are different numbers and a
// combined p95 would let fast reads hide slow writes:
//
//   reads   p95 < 300 ms
//   writes  p95 < 500 ms
//
// Accounts are created and demo-seeded in setup(), not per iteration. Two
// reasons: registration is rate limited, so 50 VUs registering in a loop would
// measure the limiter rather than the API; and an empty account has nothing to
// read. Each seeded user carries ~300 transactions, which is what makes the
// dashboard aggregation queries do real work.

import http from "k6/http";
import { check, fail, sleep } from "k6";
import { Counter, Trend } from "k6/metrics";

const BASE = __ENV.BASE_URL || "http://localhost:8000";
const API = `${BASE}/api/v1`;

// Fewer accounts than VUs, deliberately. Several users hitting one account is
// closer to the cache behaviour of a real workload than perfect isolation, and
// seeding 50 accounts would dominate the run.
const ACCOUNTS = Number(__ENV.ACCOUNTS || 10);
const PASSWORD = "LoadTest1234!";

const readLatency = new Trend("read_latency", true);
const writeLatency = new Trend("write_latency", true);

// Sample counts, thresholded below. A Trend cannot carry a `count` threshold,
// so proving the run measured anything needs its own metric.
const readSamples = new Counter("read_samples");
const writeSamples = new Counter("write_samples");

export const options = {
  stages: [
    { duration: "30s", target: 50 }, // ramp
    { duration: "2m", target: 50 }, // hold at the target concurrency
    { duration: "30s", target: 0 }, // ramp down
  ],
  thresholds: {
    // The `count` thresholds are not padding. k6 evaluates a threshold over the
    // samples it has, so a run that collects none reports `p(95)=0s` and a
    // green tick — which is exactly what happened the first time this script
    // ran and setup() failed. A budget met by taking no measurements is not met.
    "read_latency": ["p(95)<300"],
    "write_latency": ["p(95)<500"],
    "read_samples": ["count>1000"],
    "write_samples": ["count>300"],
    // A latency budget met by failing fast is not met either.
    "http_req_failed": ["rate<0.01"],
  },
};

export function setup() {
  const users = [];

  for (let i = 0; i < ACCOUNTS; i++) {
    const email = `load-${Date.now()}-${i}@example.com`;

    const registered = http.post(
      `${API}/auth/register`,
      JSON.stringify({ email, password: PASSWORD, display_name: `Load ${i}` }),
      { headers: { "Content-Type": "application/json" } },
    );

    if (registered.status !== 201) {
      fail(`setup: registration returned ${registered.status} — ${registered.body}`);
    }

    const token = registered.json("access_token");
    const auth = { headers: { Authorization: `Bearer ${token}` } };

    // A year of realistic data, so reads below are not measuring an empty table.
    const seeded = http.post(`${API}/imports/demo-seed`, null, auth);
    if (seeded.status !== 201) {
      fail(`setup: demo seed returned ${seeded.status} — ${seeded.body}`);
    }

    // The account the writes below post into. Demo-seeded users always have
    // at least one; a write needs a real id, and inventing one would measure
    // the validation path rather than the insert.
    const accounts = http.get(`${API}/accounts`, auth);
    if (accounts.status !== 200) {
      fail(`setup: accounts returned ${accounts.status} — ${accounts.body}`);
    }
    const accountId = accounts.json("0.id");
    if (!accountId) {
      fail("setup: demo-seeded user has no account, so writes cannot be measured");
    }

    users.push({ token, accountId });
  }

  return { users };
}

export default function (data) {
  const user = data.users[__VU % data.users.length];
  const auth = {
    headers: { Authorization: `Bearer ${user.token}`, "Content-Type": "application/json" },
  };

  // --- reads ---------------------------------------------------------------
  // The composite dashboard first: it is the heaviest read in the product and
  // the one a user waits on before anything else is visible.
  const dashboard = http.get(`${API}/analytics/dashboard`, auth);
  readLatency.add(dashboard.timings.duration);
  readSamples.add(1);
  check(dashboard, { "dashboard 200": (r) => r.status === 200 });

  const transactions = http.get(`${API}/transactions?limit=50`, auth);
  readLatency.add(transactions.timings.duration);
  readSamples.add(1);
  check(transactions, { "transactions 200": (r) => r.status === 200 });

  const health = http.get(`${API}/health-score`, auth);
  readLatency.add(health.timings.duration);
  readSamples.add(1);
  check(health, { "health score 200": (r) => r.status === 200 });

  // --- write ---------------------------------------------------------------
  // One write per iteration, roughly the read:write ratio of the real product —
  // people look at their finances far more often than they record something.
  const created = http.post(
    `${API}/transactions`,
    JSON.stringify({
      account_id: user.accountId,
      kind: "expense",
      // Amount is unsigned; `kind` carries the direction (ADR-003 keeps money a
      // Decimal, and the string form avoids a float ever existing).
      amount: "249.00",
      currency: "INR",
      occurred_on: new Date().toISOString().slice(0, 10),
      merchant_raw: `Load Test ${__VU}-${__ITER}`,
      // Each iteration writes a genuinely new row. Without this the content
      // hash matches a previous one and idempotency returns early (ADR-007),
      // which would time a no-op and report it as a fast write.
      allow_duplicate: true,
    }),
    auth,
  );
  writeLatency.add(created.timings.duration);
  writeSamples.add(1);
  check(created, { "write created": (r) => r.status === 201 });

  sleep(1);
}
