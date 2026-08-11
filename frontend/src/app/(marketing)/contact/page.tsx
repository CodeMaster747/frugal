import type { Metadata } from "next";
import Link from "next/link";

import { DocPage, DocSection } from "@/features/marketing/components/doc-page";
import { SUPPORT_EMAIL } from "@/features/marketing/content";

export const metadata: Metadata = {
  title: "Contact — Frugal",
  description:
    "How to report a bug, request a feature, send feedback, or disclose a security issue in Frugal.",
};

/**
 * Deliberately not a form.
 *
 * There is no endpoint behind one, and a form that accepts your report and
 * silently drops it is worse than a page that tells you where to send it. Each
 * section says what to include, because a report with a request ID in it is
 * worth more than five without.
 */
export default function ContactPage() {
  return (
    <DocPage
      eyebrow="Contact Us"
      title="Get in touch"
      intro="Everything below goes to the same inbox. The sections differ only in what is worth including, which is the part that decides how quickly anything can be done about it."
    >
      <DocSection id="bug" title="Report a bug">
        <p>
          Send it to <MailLink subject="Bug report" />. What helps, roughly in order:
        </p>
        <ul className="list-disc space-y-2 pl-5">
          <li>
            <span className="text-ink">The request ID.</span> Every error Frugal shows carries
            one. It maps straight to the server-side trace for that exact request, which usually
            turns a search into a lookup.
          </li>
          <li>
            <span className="text-ink">What you did, in order.</span> The screen, the action,
            and what you expected instead of what happened.
          </li>
          <li>
            <span className="text-ink">Browser and theme.</span> Light and dark are separate
            palettes here, not an inversion, so a contrast or visibility problem in one may not
            exist in the other.
          </li>
        </ul>
        <p>
          If the product seems broadly broken rather than broken in one place, check{" "}
          <InlineLink href="/support#status">System status</InlineLink> first — a backend that
          is down looks like a great many separate bugs.
        </p>
      </DocSection>

      <DocSection id="feature" title="Request a feature">
        <p>
          Send it to <MailLink subject="Feature request" />. The most useful requests describe
          the decision you were trying to make and could not, rather than the control you
          imagined. Frugal is built around explained recommendations, so &ldquo;I could not tell
          why the verdict changed&rdquo; is a more actionable request than &ldquo;add a
          chart&rdquo;.
        </p>
        <p>
          Some things are known gaps rather than oversights, and saying so is more honest than
          pretending they are coming: bank account linking, multi-currency conversion, shared or
          household accounts, and a mobile app are all out of scope for the current version.
        </p>
      </DocSection>

      <DocSection id="feedback" title="Send feedback">
        <p>
          Send it to <MailLink subject="Feedback" />. Impressions are genuinely useful,
          particularly about the explanations: if a score or a verdict was technically
          decomposed and still did not tell you anything, that is a design failure worth hearing
          about even when nothing is broken.
        </p>
        <p>
          The same goes for the numbers themselves. A verdict that felt wrong for your situation
          is a data point about the rubric, and the rubric is versioned precisely so its weights
          can be revised without invalidating what it said before.
        </p>
      </DocSection>

      <DocSection id="security" title="Security disclosure">
        <p>
          Please report suspected vulnerabilities privately to{" "}
          <MailLink subject="Security disclosure" /> rather than in a public issue, and give us
          a chance to fix it before disclosure.
        </p>
        <p>
          Include the affected endpoint or screen, the steps to reproduce, and what an attacker
          would gain. If it involves account data, please use a test account of your own rather
          than anyone else&rsquo;s — there is no scenario where we need someone else&rsquo;s
          data to confirm a report.
        </p>
        <p>
          If you believe your own account has been compromised, sign out of all sessions first:
          that revokes the refresh-token family server-side and invalidates anything issued from
          it.
        </p>
      </DocSection>
    </DocPage>
  );
}

function MailLink({ subject }: { subject: string }) {
  return (
    <a
      href={`mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(`Frugal — ${subject}`)}`}
      className="rounded-control text-ink underline underline-offset-4"
    >
      {SUPPORT_EMAIL}
    </a>
  );
}

function InlineLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link href={href} className="rounded-control text-ink underline underline-offset-4">
      {children}
    </Link>
  );
}
