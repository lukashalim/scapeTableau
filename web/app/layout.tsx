import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";

import { ThemeProvider } from "@/components/theme-provider";
import { WorkbookProvider } from "@/components/workbook-session";
import { ThemeToggle } from "@/components/theme-toggle";
import { Toaster } from "@/components/ui/sonner";
import { DEFAULT_DESCRIPTION, DEFAULT_TITLE, SITE_NAME, SITE_URL } from "@/lib/site";

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
  metadataBase: new URL(SITE_URL),
  title: {
    default: DEFAULT_TITLE,
    template: `%s · ${SITE_NAME}`,
  },
  description: DEFAULT_DESCRIPTION,
  applicationName: SITE_NAME,
  alternates: {
    canonical: SITE_URL,
  },
  openGraph: {
    type: "website",
    locale: "en_US",
    url: SITE_URL,
    siteName: SITE_NAME,
    title: DEFAULT_TITLE,
    description: DEFAULT_DESCRIPTION,
    images: [
      {
        url: "/og.png",
        width: 1200,
        height: 630,
        alt: "BIEscape — open Tableau workbooks in the browser",
      },
      {
        url: "/opengraph-image",
        width: 1200,
        height: 630,
        alt: "BIEscape — open Tableau workbooks in the browser",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: DEFAULT_TITLE,
    description: DEFAULT_DESCRIPTION,
    images: ["/og.png", "/opengraph-image"],
  },
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
          <WorkbookProvider>
            <header className="sticky top-0 z-20 flex items-center justify-between gap-4 border-b bg-background/90 px-4 py-3 backdrop-blur md:px-8">
              <Link href="/" className="font-medium tracking-tight">
                {SITE_NAME}
              </Link>
              <div className="flex items-center gap-4">
                <Link href="/tableau-to-power-bi" className="text-sm font-medium underline-offset-4 hover:underline">
                  Tableau to Power BI
                </Link>
                <ThemeToggle />
              </div>
            </header>
            {children}
            <Toaster />
          </WorkbookProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
