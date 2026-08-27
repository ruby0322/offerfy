type Props = {
  label?: string;
  children: React.ReactNode;
};

export default function Callout({ label, children }: Props) {
  return (
    <aside className="blog-callout">
      {label ? <p className="blog-callout-label">{label}</p> : null}
      <div>{children}</div>
    </aside>
  );
}
