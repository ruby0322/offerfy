import { createElement, type ComponentProps } from "react";
import type { MDXRemoteProps } from "next-mdx-remote/rsc";
import Callout from "./Callout";
import CtaRow from "./CtaRow";
import Figure from "./Figure";

/**
 * Everything Figure takes except `slug`, which the map injects.
 *
 * `Omit` rather than a hand-written `Pick`: the list used to name the three
 * props that existed when it was written, so a prop added to Figure was
 * accepted by the type checker at the call site in MDX, dropped silently
 * here, and showed up as a figure that ignored it. Deriving the type means a
 * new prop works without editing this file.
 */
type FigureProps = Omit<ComponentProps<typeof Figure>, "slug">;

export function mdxComponents(slug: string): NonNullable<MDXRemoteProps["components"]> {
  return {
    Callout,
    CtaRow,
    // `slug` last, so a post cannot point a figure at another post's images.
    Figure: (props: FigureProps) => createElement(Figure, { ...props, slug }),
  };
}
