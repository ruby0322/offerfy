type Props = {
  slug: string;
  src: string;
  alt: string;
};

export default function Figure({ slug, src, alt }: Props) {
  return (
    <figure className="blog-figure">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={`/blog-media/${encodeURIComponent(slug)}/${encodeURIComponent(src)}`} alt={alt} />
    </figure>
  );
}
