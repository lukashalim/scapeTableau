import { ImageResponse } from "next/og";

export const alt = "BIEscape — open Tableau workbooks in the browser";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OpenGraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: 72,
          background: "#0b1220",
          color: "#f8fafc",
        }}
      >
        <div style={{ fontSize: 28, letterSpacing: 4, textTransform: "uppercase", color: "#94a3b8" }}>
          BIESCAPE
        </div>
        <div style={{ marginTop: 24, fontSize: 64, fontWeight: 600, lineHeight: 1.1, maxWidth: 980 }}>
          Open Tableau workbooks in the browser
        </div>
        <div style={{ marginTop: 28, fontSize: 28, color: "#cbd5e1", maxWidth: 900 }}>
          SQL, DAX, and a Power BI project for Desktop.
        </div>
      </div>
    ),
    size,
  );
}
