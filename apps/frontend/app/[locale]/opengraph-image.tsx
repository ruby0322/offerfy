import { ImageResponse } from "next/og";

export const alt = "Offerfy";
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
          padding: "72px 80px",
          background: "#F6F1E8",
          color: "#1C1914",
        }}
      >
        <div
          style={{
            width: 8,
            height: 8,
            background: "#A35C3A",
            marginBottom: 20,
          }}
        />
        <div
          style={{
            fontSize: 56,
            lineHeight: 1.15,
            letterSpacing: "-0.02em",
            fontWeight: 600,
            maxWidth: 900,
          }}
        >
          The AI resume editor you’ll keep using.
        </div>
        <div style={{ marginTop: 20, fontSize: 28, color: "#5C564E" }}>
          Chat edits this file. No account needed.
        </div>
      </div>
    ),
    { ...size },
  );
}
