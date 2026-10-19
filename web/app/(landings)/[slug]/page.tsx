import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { SeoLanding } from "@/components/seo-landing";
import { getLandingPage, LANDING_PAGES, SITE_NAME, SITE_URL } from "@/lib/site";

interface LandingRouteProps {
  params: Promise<{ slug: string }>;
}

export function generateStaticParams() {
  return LANDING_PAGES.map((page) => ({ slug: page.slug }));
}

export const dynamicParams = false;

export async function generateMetadata({ params }: LandingRouteProps): Promise<Metadata> {
  const { slug } = await params;
  const page = getLandingPage(slug);
  if (!page) {
    return {};
  }
  const url = `${SITE_URL}/${page.slug}`;
  return {
    title: { absolute: page.title },
    description: page.description,
    alternates: { canonical: url },
    openGraph: {
      title: page.title,
      description: page.description,
      url,
      siteName: SITE_NAME,
      type: "website",
    },
    twitter: {
      card: "summary_large_image",
      title: page.title,
      description: page.description,
    },
  };
}

export default async function LandingPageRoute({ params }: LandingRouteProps) {
  const { slug } = await params;
  const page = getLandingPage(slug);
  if (!page) {
    notFound();
  }
  return <SeoLanding page={page} />;
}
