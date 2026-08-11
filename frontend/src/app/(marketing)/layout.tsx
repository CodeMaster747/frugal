import { MarketingFooter } from "@/features/marketing/components/marketing-footer";
import { MarketingHeader } from "@/features/marketing/components/marketing-header";

/**
 * The public shell: home screen and the three pages its footer points at.
 *
 * No auth guard. This group is the only part of the app an anonymous visitor is
 * meant to reach, which is the whole point of it — before this existed, `/` was
 * the dashboard and the product's first screen was a password field.
 *
 * Typed as a plain children prop rather than `LayoutProps<"/">`: the same
 * layout serves four routes, and the generated helper is keyed to one.
 */
export default function MarketingLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-full flex-1 flex-col">
      {/* Scroll reveals start at opacity 0 and are turned on from an effect, so
       * with scripting off the page would be blank. One rule fixes that; it
       * costs nothing and the alternative is a landing page that renders
       * nothing to anything that does not run JavaScript. */}
      <noscript>
        <style>{`[data-reveal]{opacity:1 !important;transform:none !important}`}</style>
      </noscript>

      <a
        href="#content"
        className="sr-only rounded-control border border-hairline bg-surface px-4 py-2 type-body font-medium focus:not-sr-only focus:absolute focus:top-3 focus:left-3 focus:z-30"
      >
        Skip to content
      </a>

      <MarketingHeader />
      <main id="content" className="flex-1">
        {children}
      </main>
      <MarketingFooter />
    </div>
  );
}
