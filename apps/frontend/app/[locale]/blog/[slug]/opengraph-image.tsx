import { ImageResponse } from "next/og";
import { hasLocale } from "next-intl";
import { getPost, localeCopy } from "@/lib/blog/load";
import { routing, type AppLocale } from "@/i18n/routing";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt = "Offerfy";

function localeOf(value: string): AppLocale {
  return hasLocale(routing.locales, value) ? value : routing.defaultLocale;
}

export default async function Image({
  params,
}: {
  params: Promise<{ locale: string; slug: string }>;
}) {
  const { locale: localeParam, slug } = await params;
  const locale = localeOf(localeParam);
  const post = getPost(slug);
  const title = post ? localeCopy(post, locale).title : "Offerfy";
  const typeLabel = post?.type === "guide" ? "GUIDE" : "NOTE";

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          background: "#F6F1E8",
          color: "#1C1914",
          padding: 72,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div style={{ width: 24, height: 24, background: "#A35C3A" }} />
          <div style={{ fontSize: 28, letterSpacing: 2 }}>{typeLabel}</div>
        </div>
        <div
          style={{
            fontSize: title.length > 40 ? 56 : 68,
            lineHeight: 1.15,
            letterSpacing: -1.5,
            fontWeight: 500,
            maxWidth: 1000,
          }}
        >
          {title}
        </div>
        <div style={{ fontSize: 28, color: "#5C564E" }}>Offerfy</div>
      </div>
    ),
    { ...size },
  );
}
