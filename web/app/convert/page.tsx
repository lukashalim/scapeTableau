import type { Metadata } from "next";

import { WorkbookConverter } from "@/components/workbook-converter";

export const metadata: Metadata = {
  title: "Convert workbook",
  robots: { index: false, follow: false },
};

export default function ConvertPage() {
  return <WorkbookConverter view="results" />;
}
