"use client";

import { ThemeProvider as NextThemesProvider } from "next-themes";
import type { ComponentProps } from "react";

/**
 * Theme provider.
 *
 * `attribute="class"` stamps `.dark` on <html>, which is what the
 * `@custom-variant dark` rule in globals.css keys off. Defaults to the OS
 * setting; an explicit user choice persists and wins in both directions.
 */
export function ThemeProvider({
  children,
  ...props
}: ComponentProps<typeof NextThemesProvider>) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
      {...props}
    >
      {children}
    </NextThemesProvider>
  );
}
