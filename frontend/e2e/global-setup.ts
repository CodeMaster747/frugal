import { execFile } from "node:child_process";
import { promisify } from "node:util";

const run = promisify(execFile);

/**
 * Clear the rate limiter before the run.
 *
 * A full pass registers ~80 accounts from one IP. The limiter counts them —
 * correctly — and a few runs back to back exhaust even the generous local
 * ceiling. The symptom is not an obvious "rate limited" message: pages come
 * back empty and the `renders no console errors` specs fail with a bare
 * `429`, which reads as an application bug in whichever test happened to run
 * at the time.
 *
 * Cleared before rather than after, so the run starts from a known state
 * regardless of what was run by hand beforehand.
 */
export default async function globalSetup() {
  try {
    const { stdout } = await run("docker", [
      "compose",
      "exec",
      "-T",
      "api",
      "python",
      "-m",
      "scripts.clear_rate_limits",
    ]);
    // stderr, not stdout: reporters own stdout.
    console.error(`[setup] ${stdout.trim()}`);
  } catch (error) {
    console.warn(
      `[setup] could not clear rate limits, so a long run may start seeing 429s: ` +
        `${error instanceof Error ? error.message : String(error)}`,
    );
  }
}
