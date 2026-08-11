import type { Metadata } from "next";
import Link from "next/link";

import { SystemStatus } from "@/components/system-status";
import { DocPage, DocSection } from "@/features/marketing/components/doc-page";
import { SUPPORT_EMAIL } from "@/features/marketing/content";

export const metadata: Metadata = {
  title: "Support — Frugal",
  description:
    "Troubleshooting, account and security, how your data is handled, and a live check of whether the Frugal backend is up.",
};

export default function SupportPage() {
  return (
    <DocPage
      eyebrow="Get Support"
      title="Support"
      intro="What to try when something is not behaving, how sessions and account security work, what happens to your data, and whether the service is up right now."
    >
      <DocSection id="troubleshooting" title="Troubleshooting">
        <p>Four things account for most of what goes wrong.</p>
        <dl className="space-y-3">
          <Item term="You were signed out unexpectedly">
            Frugal rotates its refresh token on every use and watches for replay. If an old
            token is presented again, the entire session family is invalidated — including yours
            — and you are returned to sign-in with an explanation. It is a blunt response on
            purpose: the alternative is leaving a possibly stolen session alive. Signing in
            again is all that is needed.
          </Item>
          <Item term="An import found no rows, or fewer than expected">
            Check the column mapping step first — an amount column mapped to the description
            will fail every row, and each failure is listed with its reason before you commit.
            If the rows were valid but nothing was added, they were already imported: Frugal
            hashes each row and refuses duplicates, so re-importing an overlapping date range is
            a no-op rather than a doubling.
          </Item>
          <Item term="A receipt is stuck on processing">
            OCR runs in a background worker, so the page will keep showing its status rather
            than blocking. If it stays queued, the worker or its broker is likely down — check{" "}
            <InlineLink href="#status">System status</InlineLink> below. A receipt that finishes
            but has low-confidence fields will move to needs review rather than committing
            itself.
          </Item>
          <Item term="A forecast or health score says there is not enough data">
            This is a real answer, not a failure. The forecaster picks its method by how much
            history it has and names the one it used; below about 60 days it can only project
            known recurring items. The same applies to the health score, which returns a partial
            result with caveats rather than inventing the sub-metrics it cannot compute. Import
            more history, or load the demo data, and both fill in.
          </Item>
        </dl>
      </DocSection>

      <DocSection id="account" title="Account & security">
        <p>
          Passwords are hashed with Argon2id and must be at least 12 characters with upper and
          lower case and a digit. Sign-in, registration, and token refresh are rate limited per
          IP and per account.
        </p>
        <p>
          Your access token lives in memory for fifteen minutes and is never written to
          localStorage or sessionStorage, so it cannot be read by injected script and it dies
          with the tab. The long-lived refresh token is an httpOnly, Secure, SameSite=Lax cookie
          that JavaScript cannot read at all; the session survives a reload because the browser
          presents that cookie, not because a token was stashed somewhere readable.
        </p>
        <p>
          You can delete your account from Settings. It removes or anonymises all of your data,
          including any receipt images in object storage. It is not a soft delete and there is
          no recovery window, so export anything you want to keep first.
        </p>
      </DocSection>

      <DocSection id="privacy" title="Data & privacy">
        <p>
          Frugal holds the transaction history you give it and what it derives from that
          history. There is no bank connection, no third-party data broker, and nothing is sold
          or shared.
        </p>
        <p>
          Receipt images are uploaded straight to object storage through a presigned URL — the
          bytes never pass through the API — and are removed when you delete the receipt or the
          account. Every record is scoped to your user, and a structural test asserts that every
          table carrying user data has a cascading foreign key, so a new table cannot quietly
          escape deletion.
        </p>
        <p>
          Scores and forecasts are computed from your own data on demand. Nothing you enter is
          used to train a model that serves anyone else; the categorisation model learns from
          your corrections for your account.
        </p>
      </DocSection>

      <DocSection id="status" title="System status">
        <p>
          A live readiness check against the API and its dependencies. If this reports the
          backend as unreachable or degraded, that explains most failures elsewhere in the
          product and there is nothing to fix on your side.
        </p>
        <div className="pt-1">
          <SystemStatus />
        </div>
        <p>
          Still stuck?{" "}
          <a
            href={`mailto:${SUPPORT_EMAIL}`}
            className="rounded-control text-ink underline underline-offset-4"
          >
            Email us
          </a>{" "}
          or see <InlineLink href="/contact">Contact us</InlineLink> for what to include.
        </p>
      </DocSection>
    </DocPage>
  );
}

function Item({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <dt className="type-body font-medium text-ink">{term}</dt>
      <dd className="type-body text-ink-secondary">{children}</dd>
    </div>
  );
}

function InlineLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link href={href} className="rounded-control text-ink underline underline-offset-4">
      {children}
    </Link>
  );
}
