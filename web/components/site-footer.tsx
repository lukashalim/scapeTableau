import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { DESKTOP_RELEASE_URL, GITHUB_URL, LANDING_PAGES, SITE_URL } from "@/lib/site";
import { cn } from "@/lib/utils";

export function SiteFooter() {
  return (
    <footer className="border-t bg-muted/30">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-12 md:py-16">
        <h2 className="font-heading text-2xl font-semibold tracking-tight md:text-3xl">
          Ready to leave the workbook black box?
        </h2>
        <p className="max-w-2xl text-muted-foreground">
          Try the hosted converter, clone the repo for CLI/Streamlit, or download the free desktop app for Hyper →
          Excel. MIT licensed.
        </p>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:flex-wrap">
          <Link href="/#converter" className={cn(buttonVariants({ size: "lg" }))}>
            Try the Web App
          </Link>
          <a
            href={DESKTOP_RELEASE_URL}
            target="_blank"
            rel="noreferrer"
            className={cn(buttonVariants({ size: "lg" }))}
          >
            Download desktop converter
          </a>
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noreferrer"
            className={cn(buttonVariants({ variant: "outline", size: "lg" }))}
          >
            Clone the repo
          </a>
        </div>
        <nav aria-label="Landing pages" className="flex flex-wrap gap-x-4 gap-y-2 text-sm">
          {LANDING_PAGES.map((page) => (
            <Link key={page.slug} href={`/${page.slug}`} className="text-muted-foreground underline-offset-4 hover:underline">
              {page.h1.split(":")[0]}
            </Link>
          ))}
        </nav>
        <p className="font-mono text-xs text-muted-foreground">
          git clone {GITHUB_URL}.git && pip install -r requirements.txt && python cli.py process
        </p>
        <p className="text-xs text-muted-foreground">
          {SITE_URL} · source stays at {GITHUB_URL}
        </p>
      </div>
    </footer>
  );
}
