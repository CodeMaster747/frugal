import Link from "next/link";

import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { GetStartedButton } from "@/features/marketing/components/get-started-button";

/**
 * The public header.
 *
 * Same 56px brand block as the sign-in shell and the dashboard sidebar, so the
 * three surfaces read as one product. Sticky, because the page is long and the
 * call to action should not be something you have to scroll back for; opaque
 * `bg-page` rather than a blur, which is not a material this design system has.
 *
 * There is no section-anchor navigation. A four-item menu pointing at anchors
 * on the same screen is furniture, not wayfinding. "Sign in" is not redundant
 * with "Get Started" — it is the only route for a returning user, since the CTA
 * goes to registration — but it folds away under 640px, where the register page
 * carries a link to it anyway.
 */
export function MarketingHeader() {
  return (
    <header className="sticky top-0 z-20 border-b border-hairline bg-page">
      <div className="mx-auto flex h-14 w-full items-center justify-between gap-3 px-4 sm:px-6 lg:px-8 xl:max-w-[75rem]">
        <Link href="/" className="rounded-control type-section font-semibold">
          Frugal
        </Link>

        <div className="flex items-center gap-2">
          <ThemeToggle />
          <Button variant="ghost" size="sm" className="hidden sm:inline-flex" asChild>
            <Link href="/login">Sign in</Link>
          </Button>
          <GetStartedButton size="sm" />
        </div>
      </div>
    </header>
  );
}
