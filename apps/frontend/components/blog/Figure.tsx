type Props = {
  slug: string;
  src: string;
  alt: string;
  /**
   * "full" is the whole image.
   *
   * "peek" is the treatment ResumeThumb's `banner` variant uses on a phone:
   * the image is laid out at the width it is given, runs past the bottom of a
   * shorter box, and a gradient takes it from there. For a screenshot whose
   * point is in its first few lines -- who sent it and what it says -- that
   * shows the part worth reading without spending a screen on the signature
   * block, and it ends the crop without drawing a second rule under the one
   * the border already draws.
   */
  variant?: "full" | "peek";
  /**
   * How much of the column the figure takes, as a CSS length.
   *
   * Applies to both variants. A phone screenshot is the case this exists
   * for: shown at the full width of the text column a 1:2 portrait runs
   * past two screens, so it wants narrowing rather than cropping. Default
   * is the whole column for `full`, and 40rem for `peek` -- at full width a
   * screenshot of someone else's UI competes with the post for the page.
   */
  width?: string;
  /**
   * The peek box's aspect ratio, as a CSS `aspect-ratio` value.
   *
   * The crop is the box, not the image: the image overflows a box shorter
   * than itself and the gradient ends it. So this is "how much do I want to
   * see", and it has to be chosen against the image's own proportions --
   * 1664/600 shows the top three-quarters of a 1664x776 email, and would
   * show a sliver of a 520x1040 phone capture. Ignored when variant is
   * "full".
   */
  ratio?: string;
};

export default function Figure({ slug, src, alt, variant = "full", width, ratio }: Props) {
  const peek = variant === "peek";
  // Only the vars the caller set: an unset var falls back to the default in
  // the stylesheet, which is what keeps every existing Figure unchanged.
  const style: Record<string, string> = {};
  if (width) style["--blog-figure-width"] = width;
  if (peek && ratio) style["--blog-figure-ratio"] = ratio;

  return (
    <figure
      className={peek ? "blog-figure blog-figure-peek" : "blog-figure"}
      style={Object.keys(style).length > 0 ? (style as React.CSSProperties) : undefined}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={`/blog-media/${encodeURIComponent(slug)}/${encodeURIComponent(src)}`} alt={alt} />
      {peek ? <span className="blog-figure-fade" aria-hidden="true" /> : null}
    </figure>
  );
}
