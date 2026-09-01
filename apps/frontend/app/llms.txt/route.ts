import { NextResponse } from "next/server";
import { llmsTxt } from "@/lib/agent-markdown";

export function GET() {
  return new NextResponse(llmsTxt(), {
    headers: {
      "Content-Type": "text/markdown; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
}
