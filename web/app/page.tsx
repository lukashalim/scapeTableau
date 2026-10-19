import { Marketing } from "@/components/marketing";
import { WorkbookConverter } from "@/components/workbook-converter";

export default function HomePage() {
  return (
    <>
      <Marketing />
      <div id="converter" className="scroll-mt-20 border-t">
        <WorkbookConverter />
      </div>
    </>
  );
}
