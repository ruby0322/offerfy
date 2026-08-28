import { ImageResponse } from "next/og";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const alt = "Offerfy";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const PAPER = "#F6F1E8";
const INK = "#1C1914";
const MUTED = "#5C564E";
const CLAY = "#A35C3A";
const SHEET = "#FFFBF7";
const RULE = "#E3D8C8";
const HIGHLIGHT = "#EEDED5";

const ogFontsDir = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../lib/og-fonts",
);
const fraunces = await readFile(join(ogFontsDir, "Fraunces-latin-500.ttf"));
const sourceSans400 = await readFile(
  join(ogFontsDir, "SourceSans3-latin-400.ttf"),
);
const sourceSans600 = await readFile(
  join(ogFontsDir, "SourceSans3-latin-600.ttf"),
);
const sourceSans700 = await readFile(
  join(ogFontsDir, "SourceSans3-latin-700.ttf"),
);
const plexMono = await readFile(join(ogFontsDir, "IBMPlexMono-latin-400.ttf"));

function srcLine(text: string, highlight = false) {
  return (
    <div
      style={{
        display: "flex",
        fontFamily: "IBM Plex Mono",
        whiteSpace: "pre",
        minHeight: 35,
        alignSelf: highlight ? "flex-start" : "stretch",
        color: highlight ? INK : MUTED,
      }}
    >
      {text.length > 0 ? text : " "}
    </div>
  );
}

function tab(label: string, on = false) {
  return (
    <div
      style={{
        display: "flex",
        fontSize: 24,
        fontWeight: 600,
        fontFamily: "Source Sans 3",
        padding: "8px 16px",
        borderRadius: 8,
        color: on ? SHEET : MUTED,
        background: on ? INK : "transparent",
      }}
    >
      {label}
    </div>
  );
}

function bullet(text: string) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "row",
        alignItems: "flex-start",
        fontSize: 24,
        lineHeight: 1.4,
        color: INK,
        fontFamily: "Source Sans 3",
      }}
    >
      <div
        style={{
          display: "flex",
          width: 22,
          flexShrink: 0,
        }}
      >
        •
      </div>
      <div style={{ display: "flex", flex: 1 }}>{text}</div>
    </div>
  );
}

export default function OpenGraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: 1200,
          height: 630,
          display: "flex",
          position: "relative",
          overflow: "hidden",
          background: PAPER,
          color: INK,
        }}
      >
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            justifyContent: "center",
            position: "absolute",
            left: 56,
            top: 0,
            width: 500,
            height: 630,
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              marginBottom: 22,
            }}
          >
            <div
              style={{
                width: 8,
                height: 8,
                flexShrink: 0,
                background: CLAY,
              }}
            />
            <div
              style={{
                display: "flex",
                fontFamily: "Source Sans 3",
                fontSize: 26,
                fontWeight: 600,
                letterSpacing: "-0.01em",
              }}
            >
              Offerfy
            </div>
          </div>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              fontFamily: "Fraunces",
              fontSize: 48,
              fontWeight: 500,
              lineHeight: 1.12,
              letterSpacing: "-0.03em",
            }}
          >
            <div style={{ display: "flex", fontFamily: "Fraunces" }}>
              The AI resume editor
            </div>
            <div style={{ display: "flex", fontFamily: "Fraunces" }}>
              you’ll keep using.
            </div>
          </div>
        </div>

        <div
          style={{
            display: "flex",
            flexDirection: "column",
            position: "absolute",
            left: 504,
            top: 68,
            width: 1028,
            height: 820,
            background: SHEET,
            border: `1px solid ${RULE}`,
            fontFamily: "Source Sans 3",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "baseline",
              justifyContent: "space-between",
              padding: "22px 28px",
              borderBottom: `1px solid ${RULE}`,
              flexShrink: 0,
            }}
          >
            <div
              style={{
                display: "flex",
                fontSize: 32,
                fontWeight: 700,
                fontFamily: "Source Sans 3",
              }}
            >
              Offerfy
            </div>
            <div
              style={{
                display: "flex",
                fontSize: 26,
                fontWeight: 400,
                fontFamily: "Source Sans 3",
                color: MUTED,
              }}
            >
              Resume
            </div>
          </div>
          <div
            style={{
              display: "flex",
              gap: 8,
              padding: "14px 22px",
              borderBottom: `1px solid ${RULE}`,
              flexShrink: 0,
            }}
          >
            {tab("Typst", true)}
            {tab("Chat")}
            {tab("Template")}
          </div>
          <div
            style={{
              display: "flex",
              flex: 1,
            }}
          >
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                width: "44%",
                borderRight: `1px solid ${RULE}`,
                padding: "24px 26px",
                fontFamily: "IBM Plex Mono",
                fontSize: 24,
                fontWeight: 400,
                lineHeight: 1.45,
                color: MUTED,
              }}
            >
              {srcLine("== Experience")}
              {srcLine(" ")}
              {srcLine("#work(")}
              {srcLine('  title: "Software Engineer",')}
              {srcLine('  company: "Offerfy",')}
              {srcLine(")")}
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignSelf: "flex-start",
                  background: HIGHLIGHT,
                }}
              >
                {srcLine("- Built the best AI resume", true)}
                {srcLine("  editor in the world", true)}
              </div>
              {srcLine("- Chat edits this file;")}
              {srcLine("  the PDF updates with it")}
              {srcLine("- ATS checks on the PDF —")}
              {srcLine("  parseability, not hireability")}
              {srcLine(" ")}
              {srcLine("#work(")}
              {srcLine('  title: "Software Engineer",')}
              {srcLine('  company: "Google",')}
              {srcLine(")")}
              {srcLine("- Shipped Search infra used")}
              {srcLine("  across ads ranking")}
              {srcLine("- Cut p99 latency on the")}
              {srcLine("  query path")}
            </div>
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                width: "56%",
                padding: "24px 28px",
              }}
            >
              <div
                style={{
                  display: "flex",
                  fontSize: 18,
                  fontWeight: 600,
                  fontFamily: "Source Sans 3",
                  letterSpacing: "0.16em",
                  textTransform: "uppercase",
                  color: CLAY,
                }}
              >
                Experience
              </div>
              <div
                style={{
                  display: "flex",
                  marginTop: 16,
                  fontSize: 26,
                  fontWeight: 700,
                  fontFamily: "Source Sans 3",
                }}
              >
                Software Engineer
              </div>
              <div
                style={{
                  display: "flex",
                  marginTop: 4,
                  fontSize: 26,
                  fontWeight: 400,
                  fontFamily: "Source Sans 3",
                  color: MUTED,
                }}
              >
                Offerfy
              </div>
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  marginTop: 16,
                  paddingLeft: 0,
                }}
              >
                {bullet("Built the best AI resume editor in the world")}
                {bullet("Chat edits this file; the PDF updates with it")}
                {bullet("ATS checks on the PDF — parseability, not hireability")}
              </div>
              <div
                style={{
                  display: "flex",
                  marginTop: 32,
                  fontSize: 26,
                  fontWeight: 700,
                  fontFamily: "Source Sans 3",
                }}
              >
                Software Engineer
              </div>
              <div
                style={{
                  display: "flex",
                  marginTop: 4,
                  fontSize: 26,
                  fontWeight: 400,
                  fontFamily: "Source Sans 3",
                  color: MUTED,
                }}
              >
                Google
              </div>
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  marginTop: 16,
                }}
              >
                {bullet("Shipped Search infra used across ads ranking")}
                {bullet("Cut p99 latency on the query path")}
              </div>
            </div>
          </div>
        </div>
      </div>
    ),
    {
      ...size,
      fonts: [
        {
          name: "Source Sans 3",
          data: sourceSans400,
          style: "normal",
          weight: 400,
        },
        {
          name: "Source Sans 3",
          data: sourceSans600,
          style: "normal",
          weight: 600,
        },
        {
          name: "Source Sans 3",
          data: sourceSans700,
          style: "normal",
          weight: 700,
        },
        {
          name: "IBM Plex Mono",
          data: plexMono,
          style: "normal",
          weight: 400,
        },
        { name: "Fraunces", data: fraunces, style: "normal", weight: 500 },
      ],
    },
  );
}
