import type { Metadata } from "next";

import { MarketingBelow, MarketingHero, MarketingPrivacyNote } from "@/components/marketing";
import { WorkbookConverter } from "@/components/workbook-converter";
import { DEFAULT_DESCRIPTION, DEFAULT_TITLE, SITE_URL } from "@/lib/site";

export const metadata: Metadata = {
  title: { absolute: DEFAULT_TITLE },
  description: DEFAULT_DESCRIPTION,
  alternates: { canonical: SITE_URL },
  openGraph: {
    title: DEFAULT_TITLE,
    description: DEFAULT_DESCRIPTION,
    url: SITE_URL,
  },
  twitter: {
    card: "summary_large_image",
    title: DEFAULT_TITLE,
    description: DEFAULT_DESCRIPTION,
  },
};

export default function HomePage() {
  return (
    <>
      <MarketingHero />
      <div id="converter" className="scroll-mt-20">
        <WorkbookConverter embedded />
      </div>
      <MarketingPrivacyNote />
      <MarketingBelow />
    </>
  );
}
