import { markFor, monogram } from "@/components/employer-marks";

type Props = {
  company: string;
  /** "sm" is the live board's 34px tile; the default is the 42px list tile. */
  size?: "sm" | "md";
};

/**
 * The employer's mark, or two letters on Offerfy's own paper when we have no
 * mark for it (5 of 90 boards — see components/employer-marks.ts).
 *
 * Always aria-hidden: the company name is already in the text beside it in
 * every call site, so announcing it again would read the employer twice.
 */
export default function EmployerLogo({ company, size = "md" }: Props) {
  const mark = markFor(company);
  return (
    <span className={`job-logo${size === "sm" ? " job-logo-sm" : ""}`} aria-hidden="true">
      {mark ? (
        <svg viewBox="0 0 24 24" fill={mark.hex ?? "var(--ink)"} fillRule={mark.fillRule}>
          <path d={mark.d} />
        </svg>
      ) : (
        <span className="job-logo-mono">{monogram(company)}</span>
      )}
    </span>
  );
}
