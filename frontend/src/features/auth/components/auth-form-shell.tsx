import Link from "next/link";
import type { ReactNode } from "react";

import { ThemeToggle } from "@/components/theme-toggle";

export function AuthFormShell({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle: string;
  children: ReactNode;
  footer: ReactNode;
}) {
  return (
    <div className="flex flex-1 flex-col">
      {/* Same 56px brand block as the home screen and the dashboard sidebar, so
       * the three surfaces do not read as different products. The wordmark
       * returns to the public home rather than the app: someone who has reached
       * a sign-in form may well want to go back and read about the product. */}
      <header className="flex h-14 shrink-0 items-center justify-between px-4 sm:px-6">
        <Link href="/" className="rounded-control type-section font-semibold">
          Frugal
        </Link>
        <ThemeToggle />
      </header>

      <main className="flex flex-1 items-center justify-center px-4 py-10 sm:px-6">
        <div className="w-full max-w-sm space-y-8">
          <div className="space-y-2">
            <h1 className="type-title">{title}</h1>
            <p className="type-body text-ink-secondary">{subtitle}</p>
          </div>
          {children}
          <p className="text-center type-body text-ink-secondary">{footer}</p>
        </div>
      </main>
    </div>
  );
}
