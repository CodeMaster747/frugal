import type { Metadata } from "next";
import Link from "next/link";

import { DocPage, DocSection } from "@/features/marketing/components/doc-page";

export const metadata: Metadata = {
  title: "Learn to use Frugal",
  description:
    "How to get your data into Frugal and how to read what it gives back — imports, receipts, the health score, and the Purchase Advisor.",
};

export default function GuidePage() {
  return (
    <DocPage
      eyebrow="Learn to Use"
      title="How to use Frugal"
      intro="Five passages, in the order you will need them. Each one says what the product does and, where it matters, what it deliberately will not do."
    >
      <DocSection id="start" title="Quick start">
        <p>
          Create an account, then choose how to give Frugal something to work with. Every engine
          in the product is derived from your transaction history, so an empty account shows you
          an empty product — that is a designed state, not a bug.
        </p>
        <ol className="list-decimal space-y-2 pl-5">
          <li>
            <span className="text-ink">Load demo data.</span> One click seeds twelve months of
            realistic transactions. This is the fastest way to see what every screen looks like
            when it has something to say.
          </li>
          <li>
            <span className="text-ink">Or import a bank CSV.</span> See{" "}
            <InlineLink href="#import">Importing transactions</InlineLink> below.
          </li>
          <li>
            <span className="text-ink">Or add one transaction by hand.</span> Takes about ten
            seconds and is enough to get the dashboard started.
          </li>
        </ol>
        <p>
          From there the Overview page is the entry point. The sidebar groups the rest into
          Insights (health, forecast, what-if, should-I-buy-it) and Market (watchlist, alerts).
        </p>
      </DocSection>

      <DocSection id="import" title="Importing transactions">
        <p>
          Upload a CSV from your bank and Frugal shows you a column-mapping step before anything
          is written. Map the amount, date, and description columns, then review the parsed
          preview — rows that fail validation are listed individually with the reason, and you
          commit the import knowing exactly what will land.
        </p>
        <p>
          Re-importing the same file is safe. Each row carries a content hash under a unique
          index, so a duplicate import adds nothing rather than doubling your history. This
          means you can re-download an overlapping statement range without having to work out
          where the overlap begins.
        </p>
        <p>
          Transfers between your own accounts create a linked pair and are excluded from income
          and expense totals, so moving money to savings does not read as spending.
        </p>
      </DocSection>

      <DocSection id="receipts" title="Receipts & OCR">
        <p>
          Upload a photo or PDF of a receipt (up to 10 MB) and it is processed in the
          background: perspective correction, deskew, denoise, threshold, then OCR. Frugal
          extracts the merchant, date, total, tax, and line items.
        </p>
        <p>
          Each field gets its own confidence score, not one score for the whole receipt — a
          crisp total on a crumpled receipt should not be dragged down by a smudged date. Any
          field below the confidence threshold is flagged and the receipt goes to the review
          queue, where you see the original image beside the editable fields.
        </p>
        <p>
          A receipt is never committed as a transaction on its own unless every required field
          clears the threshold, and it is checked against your existing transactions for
          duplicates first. The job status is visible throughout: queued, processing, needs
          review, committed, or failed.
        </p>
      </DocSection>

      <DocSection id="health" title="Reading your health score">
        <p>
          The health score is a single number from 0 to 100, composed of six sub-metrics:
          savings rate, emergency-fund coverage, debt-to-income, budget discipline, cash-flow
          stability, and financial growth.
        </p>
        <p>
          It is worth knowing how to read it rather than just watching it move. Open any
          sub-metric and you get its raw value, the weight it carries, its signed contribution
          to the total, and the thresholds for each band. The score comes from a published,
          versioned rubric — there is no model to take on faith, and when the weights change the
          version changes with them.
        </p>
        <p>
          If you have not given Frugal enough history for a sub-metric, it says so. You get a
          partial score with explicit caveats rather than a confident number resting on three
          weeks of data.
        </p>
      </DocSection>

      <DocSection id="advisor" title="Using the Purchase Advisor">
        <p>
          This is what the rest of the product is for. Enter something you are thinking about
          buying and its price, and Frugal returns one of four verdicts: buy now, buy on EMI,
          wait, or not recommended.
        </p>
        <p>
          The verdict arrives with an affordability score and the factors that produced it —
          liquid savings after the purchase, emergency-fund coverage, savings-rate impact,
          committed outflows, the forecast balance trough, goal delay, and existing debt. The
          weights sum to 1.00 and the contributions sum to the score, so you can check the
          arithmetic rather than trusting the headline.
        </p>
        <p>
          On a wait verdict it estimates the date the purchase becomes affordable, derived from
          your cash-flow forecast. On buy-on-EMI it models tenure options and shows the total
          interest against the cash price. Either way the opportunity cost is stated in concrete
          terms: how many days a savings goal slips, how many months of emergency fund you lose.
        </p>
        <p>
          The what-if simulator on the Insights menu is the same machinery without a specific
          product attached, for when the question is a change in income or spending rather than
          a purchase.
        </p>
      </DocSection>
    </DocPage>
  );
}

function InlineLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link href={href} className="rounded-control text-ink underline underline-offset-4">
      {children}
    </Link>
  );
}
