import { ImageResponse } from "next/og";

export const size = { width: 32, height: 32 };
export const contentType = "image/png";

export default function Icon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: 32,
          height: 32,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#E3D8C8",
        }}
      >
        <div
          style={{
            width: 30,
            height: 30,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "#FFFBF7",
          }}
        >
          <div style={{ width: 10, height: 10, background: "#A35C3A" }} />
        </div>
      </div>
    ),
    { ...size },
  );
}
