import fs from "node:fs";
import path from "node:path";

export function blogRoot(): string {
  return path.join(process.cwd(), "content/blog");
}

export function postDir(slug: string): string {
  return path.join(blogRoot(), slug);
}

export function imagePath(slug: string, filename: string): string | null {
  if (!filename || filename.includes("..") || filename.includes("/") || filename.includes("\\")) {
    return null;
  }
  const root = path.join(postDir(slug), "images");
  const resolved = path.resolve(root, filename);
  if (!resolved.startsWith(path.resolve(root))) {
    return null;
  }
  return resolved;
}

export function imageExists(slug: string, filename: string): boolean {
  const resolved = imagePath(slug, filename);
  return resolved != null && fs.existsSync(resolved);
}
