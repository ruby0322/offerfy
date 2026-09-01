import createMiddleware from "next-intl/middleware";
import { NextRequest, NextResponse } from "next/server";
import { routing } from "./i18n/routing";
import { prefersMarkdown } from "./lib/agent-markdown";

const intlMiddleware = createMiddleware(routing);

export default function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (pathname === "/agent-markdown" || pathname === "/llms.txt") {
    return NextResponse.next();
  }
  if (prefersMarkdown(request.headers.get("accept"))) {
    const url = request.nextUrl.clone();
    url.pathname = "/agent-markdown";
    url.search = "";
    const headers = new Headers(request.headers);
    headers.set("x-offerfy-markdown-path", pathname);
    return NextResponse.rewrite(url, { request: { headers } });
  }
  return intlMiddleware(request);
}

export const config = {
  matcher: [
    "/((?!api|trpc|_next|_vercel|icon(?:/|$)|apple-icon(?:/|$)|.*\\..*).*)",
    "/blog/rss.xml",
    "/(en|zh-TW|zh-CN)/blog/rss.xml",
  ],
};
