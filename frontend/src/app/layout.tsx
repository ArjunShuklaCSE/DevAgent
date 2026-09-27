import type { Metadata } from "next";

import { Nav } from "@/components/nav";
import { Providers } from "@/components/providers";
import { THEME_SCRIPT } from "@/components/theme-toggle";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "DevAgent", template: "%s · DevAgent" },
  description: "Autonomous GitHub issue solver",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="min-h-screen bg-zinc-50 font-sans text-zinc-900 antialiased dark:bg-zinc-950 dark:text-zinc-100">
        <Providers>
          <Nav />
          <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
