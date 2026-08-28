import { ImageResponse } from "next/og";

export const size = { width: 180, height: 180 };
export const contentType = "image/png";

export default function AppleIcon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: 180,
          height: 180,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#E3D8C8",
        }}
      >
        <div
          style={{
            width: 168,
            height: 168,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "#FFFBF7",
          }}
        >
          <div style={{ width: 56, height: 56, background: "#A35C3A" }} />
        </div>
      </div>
    ),
    { ...size },
  );
}
