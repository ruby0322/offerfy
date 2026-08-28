export default function BrandMark({ size = "nav" }: { size?: "nav" | "sm" }) {
  const box = size === "sm" ? "size-4" : "size-5";
  const dot = size === "sm" ? "size-1.5" : "size-[6px]";
  return (
    <span
      className={`${box} inline-flex shrink-0 items-center justify-center border border-[#e3d8c8] bg-[#fffbf7]`}
      aria-hidden="true"
    >
      <span className={`${dot} bg-[#a35c3a]`} />
    </span>
  );
}
