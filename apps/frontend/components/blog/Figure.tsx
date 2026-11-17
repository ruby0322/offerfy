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
};

export default function Figure({ slug, src, alt, variant = "full" }: Props) {
  const peek = variant === "peek";
  return (
    <figure className={peek ? "blog-figure blog-figure-peek" : "blog-figure"}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={`/blog-media/${encodeURIComponent(slug)}/${encodeURIComponent(src)}`} alt={alt} />
      {peek ? <span className="blog-figure-fade" aria-hidden="true" /> : null}
    </figure>
  );
}
