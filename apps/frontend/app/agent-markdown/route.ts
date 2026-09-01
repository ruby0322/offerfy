import { NextRequest, NextResponse } from "next/server";
import {
  markdownHeaders,
  markdownNotFound,
  marketingMarkdown,
  marketingPage,
} from "@/lib/agent-markdown";

export function GET(request: NextRequest) {
  const path =
    request.headers.get("x-offerfy-markdown-path") ||
    request.nextUrl.searchParams.get("path") ||
    "/";
  const page = marketingPage(path);
  if (!page) {
    const { status, headers } = markdownHeaders(404);
    return new NextResponse(markdownNotFound(), { status, headers });
  }
  const { status, headers } = markdownHeaders(200);
  return new NextResponse(marketingMarkdown(page), { status, headers });
}
