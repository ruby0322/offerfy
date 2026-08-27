import { createElement, type ComponentProps } from "react";
import type { MDXRemoteProps } from "next-mdx-remote/rsc";
import Callout from "./Callout";
import CtaRow from "./CtaRow";
import Figure from "./Figure";

export function mdxComponents(slug: string): NonNullable<MDXRemoteProps["components"]> {
  return {
    Callout,
    CtaRow,
    Figure: (props: Pick<ComponentProps<typeof Figure>, "src" | "alt">) =>
      createElement(Figure, { slug, src: props.src, alt: props.alt }),
  };
}
