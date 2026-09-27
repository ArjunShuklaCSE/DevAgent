import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "DevAgent",
  description: "Autonomous GitHub issue solver",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body className="bg-zinc-950 font-sans text-zinc-100 antialiased">{children}</body>
    </html>
  );
}
