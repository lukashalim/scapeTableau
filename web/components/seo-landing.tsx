import Link from "next/link";

import { FaqJsonLd } from "@/components/faq-json-ld";
import { SiteFooter } from "@/components/site-footer";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { LandingPage } from "@/lib/site";
import { DESKTOP_RELEASE_URL, GITHUB_URL } from "@/lib/site";
import { cn } from "@/lib/utils";

interface SeoLandingProps {
  page: LandingPage;
}

export function SeoLanding({ page }: SeoLandingProps) {
  return (
    <div className="flex flex-col">
      <FaqJsonLd faqs={page.faqs} />
      <section className="mx-auto flex w-full max-w-6xl flex-col gap-8 px-4 py-12 md:py-16">
        <div className="max-w-3xl space-y-5">
          <h1 className="font-heading text-4xl font-semibold tracking-tight text-balance md:text-5xl">{page.h1}</h1>
          <p className="text-lg text-muted-foreground md:text-xl">{page.lede}</p>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:flex-wrap">
            <Link href="/#converter" className={cn(buttonVariants({ size: "lg" }))}>
              Open the converter
            </Link>
            {page.slug === "twbx-to-excel" ? (
              <a
                href={DESKTOP_RELEASE_URL}
                target="_blank"
                rel="noreferrer"
                className={cn(buttonVariants({ size: "lg" }))}
              >
                Download desktop converter
              </a>
            ) : null}
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noreferrer"
              className={cn(buttonVariants({ variant: "outline", size: "lg" }))}
            >
              Clone the repo
            </a>
          </div>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>What BIEscape does</CardTitle>
              <CardDescription>
                <ul className="mt-2 list-inside list-disc space-y-2 text-muted-foreground">
                  {page.capabilities.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </CardDescription>
            </CardHeader>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Honest limits</CardTitle>
              <CardDescription>
                <ul className="mt-2 list-inside list-disc space-y-2 text-muted-foreground">
                  {page.limitations.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </CardDescription>
            </CardHeader>
          </Card>
        </div>
        <div className="max-w-3xl space-y-4">
          <h2 className="font-heading text-2xl font-semibold tracking-tight">FAQ</h2>
          <dl className="space-y-4">
            {page.faqs.map((faq) => (
              <div key={faq.question} className="rounded-xl border bg-card p-4">
                <dt className="font-medium">{faq.question}</dt>
                <dd className="mt-2 text-sm text-muted-foreground">{faq.answer}</dd>
              </div>
            ))}
          </dl>
        </div>
        <p className="max-w-3xl text-sm text-muted-foreground">
          {page.slug === "twbx-to-excel" ? (
            <>
              For Hyper extracts, download{" "}
              <a
                href={DESKTOP_RELEASE_URL}
                className="underline underline-offset-4"
                target="_blank"
                rel="noreferrer"
              >
                BIEscape Converter
              </a>{" "}
              (unsigned Windows / macOS zips). Browser conversion stays CSV/XLSX-only.
            </>
          ) : (
            <>
              Hyper → Excel uses the{" "}
              <a
                href={DESKTOP_RELEASE_URL}
                className="underline underline-offset-4"
                target="_blank"
                rel="noreferrer"
              >
                desktop converter
              </a>
              . Clone the repo at{" "}
              <a href={GITHUB_URL} className="underline underline-offset-4" target="_blank" rel="noreferrer">
                {GITHUB_URL}
              </a>
              .
            </>
          )}
        </p>
      </section>
      <SiteFooter />
    </div>
  );
}
