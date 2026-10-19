import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { ThemeProvider } from "@/components/theme-provider";
import { ThemeToggle } from "@/components/theme-toggle";
import { Toaster } from "@/components/ui/sonner";

import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "scapeTableau — Tableau calculated fields to ANSI SQL & dbt models",
  description:
    "Instantly extract and transpile Tableau calculated fields into clean ANSI SQL and dbt-ready model files. In-memory hosted conversion; air-gapped CLI and Streamlit on your machine.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col font-sans">
        <ThemeProvider>
          <header className="sticky top-0 z-20 flex items-center justify-between gap-4 border-b bg-background/90 px-4 py-3 backdrop-blur md:px-8">
            <p className="font-medium tracking-tight">scapeTableau</p>
            <ThemeToggle />
          </header>
          {children}
          <Toaster />
        </ThemeProvider>
      </body>
    </html>
  );
}
