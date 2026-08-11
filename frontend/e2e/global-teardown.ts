import { execFile } from "node:child_process";
import { promisify } from "node:util";

const run = promisify(execFile);

/**
 * Delete the accounts this run created.
 *
 * Every spec signs up a fresh user and seeds a year of demo data — roughly 80
 * users and 24,000 transactions per run — and nothing removed them. The local
 * database reached 4,819 users and 1.4 million transactions, at which point
 * signup slowed past the 40s test timeout and the suite began failing on a
 * different spec each run. The failures looked like flakiness in whichever test
 * drew the short straw; the cause was every run before it.
 *
 * Scoped by `@example.com`, which is reserved by RFC 2606 and cannot be a real
 * account, so this never reaches a user created by hand while developing.
 *
 * Failure here is reported and swallowed. A teardown that cannot clean up is
 * worth knowing about, but it must not turn a green suite red.
 */
export default async function globalTeardown() {
  try {
    const { stdout } = await run("docker", [
      "compose",
      "exec",
      "-T",
      "api",
      "python",
      "-m",
      "scripts.reset_dev_data",
      "--email-like",
      "%@example.com",
    ]);
    // stderr, not stdout: reporters own stdout, and a stray line here
    // makes the JSON and JUnit reporters emit unparseable output.
    console.error(`[teardown] ${stdout.trim()}`);
  } catch (error) {
    console.warn(
      `[teardown] could not purge test accounts, so the local database will keep ` +
        `growing: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}
