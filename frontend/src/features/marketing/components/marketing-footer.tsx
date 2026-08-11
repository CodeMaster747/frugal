import Link from "next/link";

import { FOOTER_COLUMNS } from "@/features/marketing/content";

/**
 * The public footer.
 *
 * Four columns at desktop width: the wordmark and a one-line description, then
 * the three link sections. It separates from the page with a hairline and the
 * page's own background rather than a darker band — there is no second surface
 * below `page` in this system, and inventing one for a footer would be the only
 * place in the product where depth runs downward.
 */
export function MarketingFooter() {
  const year = new Date().getFullYear();

  return (
    <footer className="border-t border-hairline">
      <div className="mx-auto w-full px-4 py-12 sm:px-6 lg:px-8 xl:max-w-[75rem]">
        <div className="grid gap-8 sm:grid-cols-2 lg:grid-cols-4">
          <div className="space-y-2">
            <Link href="/" className="rounded-control type-section font-semibold">
              Frugal
            </Link>
            <p className="max-w-prose type-body text-ink-secondary">
              The intelligent financial decision platform. Every score, verdict, and forecast
              comes with the reasoning behind it.
            </p>
          </div>

          {FOOTER_COLUMNS.map((column) => (
            <nav key={column.heading} aria-label={column.heading} className="space-y-3">
              <h2 className="type-eyebrow text-ink-muted">{column.heading}</h2>
              <ul className="space-y-2">
                {column.links.map((link) => (
                  <li key={link.href}>
                    <FooterLink href={link.href}>{link.label}</FooterLink>
                  </li>
                ))}
              </ul>
            </nav>
          ))}
        </div>

        <div className="mt-12 flex flex-wrap items-center justify-between gap-3 border-t border-hairline pt-6">
          <p className="type-meta text-ink-muted">© {year} Frugal</p>
          <p className="type-meta text-ink-muted">
            Built to be interrogated, not trusted blindly.
          </p>
        </div>
      </div>
    </footer>
  );
}

/**
 * `mailto:` is not a route, so it goes through a plain anchor. Everything else
 * is internal and uses Link, which prefetches the three support pages.
 */
function FooterLink({ href, children }: { href: string; children: React.ReactNode }) {
  const className =
    "rounded-control type-body text-ink-secondary transition-colors hover:text-ink";

  if (href.startsWith("mailto:")) {
    return (
      <a href={href} className={className}>
        {children}
      </a>
    );
  }

  return (
    <Link href={href} className={className}>
      {children}
    </Link>
  );
}
