import type { Root } from "mdast";
import { visit } from "unist-util-visit";
import { MDX_COMPONENT_NAMES } from "./types";

const ALLOWED = new Set<string>(MDX_COMPONENT_NAMES);

type JsxNode = {
  type: string;
  name?: string | null;
  attributes?: Array<{
    type: string;
    name?: string;
    value?: unknown;
  }>;
};

function attr(node: JsxNode, name: string): string | null {
  const found = node.attributes?.find(
    (item) => item.type === "mdxJsxAttribute" && item.name === name,
  );
  if (!found) return null;
  if (typeof found.value === "string") return found.value;
  return found.value == null ? null : String(found.value);
}

export function remarkBlogRules() {
  return (tree: Root) => {
    visit(tree, (node) => {
      if (node.type === "mdxjsEsm") {
        throw new Error("MDX imports are not allowed");
      }
      if (node.type === "html") {
        throw new Error("Raw HTML is not allowed");
      }
      if (node.type === "heading" && node.depth === 1) {
        throw new Error("h1 is not allowed in the post body; the title is the page H1");
      }
      if (node.type === "mdxJsxFlowElement" || node.type === "mdxJsxTextElement") {
        const jsx = node as JsxNode;
        const name = jsx.name;
        if (!name || !ALLOWED.has(name)) {
          throw new Error(`Unknown MDX component: ${name ?? "(fragment)"}`);
        }
        if (name === "Figure") {
          const alt = attr(jsx, "alt");
          const src = attr(jsx, "src");
          const variant = attr(jsx, "variant");
          const width = attr(jsx, "width");
          const ratio = attr(jsx, "ratio");
          // Both land in a CSS custom property, where a bad value is not an
          // error -- the declaration is simply dropped and the figure
          // silently keeps the default. Checked here so that shows up as a
          // failed build rather than as a figure that ignored its own props.
          if (width != null && !/^\d+(\.\d+)?(rem|em|px|%|ch)$/.test(width)) {
            throw new Error(`Figure width must be a CSS length (e.g. 18rem): ${width}`);
          }
          if (ratio != null) {
            if (variant !== "peek") {
              throw new Error("Figure ratio only applies to variant=\"peek\"");
            }
            if (!/^\d+(\.\d+)?(\s*\/\s*\d+(\.\d+)?)?$/.test(ratio)) {
              throw new Error(`Figure ratio must look like "16 / 9" or "1.5": ${ratio}`);
            }
          }
          // Checked here rather than left to default: a typo in the variant
          // would otherwise render the full image and look like the crop was
          // never asked for.
          if (variant != null && variant !== "full" && variant !== "peek") {
            throw new Error(`Figure variant must be full or peek: ${variant}`);
          }
          if (!alt?.trim()) {
            throw new Error("Figure requires a non-empty alt");
          }
          if (!src?.trim()) {
            throw new Error("Figure requires src");
          }
          if (src.includes("..") || src.includes("/") || src.includes("\\")) {
            throw new Error(`Figure src must be a filename in images/: ${src}`);
          }
        }
      }
    });
  };
}
