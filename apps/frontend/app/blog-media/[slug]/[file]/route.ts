import fs from "node:fs";
import path from "node:path";
import { NextResponse } from "next/server";
import { imagePath } from "@/lib/blog/paths";

const TYPES: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".gif": "image/gif",
  ".svg": "image/svg+xml",
};

type Props = {
  params: Promise<{ slug: string; file: string }>;
};

export async function GET(_request: Request, { params }: Props) {
  const { slug, file } = await params;
  const resolved = imagePath(slug, file);
  if (!resolved || !fs.existsSync(resolved)) {
    return new NextResponse("Not found", { status: 404 });
  }
  const ext = path.extname(resolved).toLowerCase();
  const contentType = TYPES[ext];
  if (!contentType) {
    return new NextResponse("Not found", { status: 404 });
  }
  const body = fs.readFileSync(resolved);
  return new NextResponse(body, {
    headers: {
      "Content-Type": contentType,
      "Cache-Control": "public, max-age=31536000, immutable",
    },
  });
}
