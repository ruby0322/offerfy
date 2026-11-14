"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { currentTimeZone } from "@/lib/geo";
import { useHoverMenu } from "@/lib/use-hover-menu";
import { useIsClient } from "@/lib/use-is-client";
import { fetchJobCities, type JobFacetOption } from "@/lib/jobs";

type Props = {
  country?: string;
  city?: string;
  /** Countries the catalogue has roles in, with counts. */
  countries: JobFacetOption[];
  countryLabel: (code: string) => string;
  /** Builds a URL with country/city replaced; a null clears that key. */
  hrefFor: (next: { country: string | null; city: string | null }) => string;
  /** True once the country came from the device clock rather than a click. */
  detected: boolean;
};

const PIN = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
    <path d="M12 21s7-5.6 7-11a7 7 0 1 0-14 0c0 5.4 7 11 7 11z" />
    <circle cx="12" cy="10" r="2.6" />
  </svg>
);

/**
 * Where, next to What — the two things people actually set.
 *
 * Cities are fetched when the menu opens, for the chosen country only:
 * preloading all eighteen would be eighteen queries to render a menu most
 * visitors never open. They come from the catalogue itself rather than a
 * fixed list, so a city appears the week an employer opens an office there.
 */
export default function WherePicker({
  country,
  city,
  countries,
  countryLabel,
  hrefFor,
  detected,
}: Props) {
  const t = useTranslations("jobs");
  const { open, box, hover, toggle, close } = useHoverMenu<HTMLSpanElement>();
  // One cache entry, keyed by the country it was fetched for. Keeping the
  // key in the state rather than in a second variable is what lets "still
  // loading" be derived during render instead of set from inside the effect.
  const [loaded, setLoaded] = useState<{ country: string; items: JobFacetOption[] } | null>(
    null,
  );
  const isClient = useIsClient();
  const zone = isClient ? currentTimeZone() : null;
  const cities = loaded && loaded.country === country ? loaded.items : null;

  useEffect(() => {
    if (!open || !country || loaded?.country === country) return;
    let live = true;
    fetchJobCities(country).then((items) => {
      if (live) setLoaded({ country, items });
    });
    return () => {
      live = false;
    };
  }, [open, country, loaded]);

  const caption = country
    ? city
      ? `${countryLabel(country)} · ${city}`
      : countryLabel(country)
    : t("whereAnywhere");

  return (
    <span
      className="jobs-where"
      data-open={open ? "1" : "0"}
      ref={box}
      {...hover}
    >
      <button
        type="button"
        className="jobs-where-btn"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={toggle}
      >
        {PIN}
        <span className="jobs-where-cap">{caption}</span>
        <span className="jobs-where-caret" aria-hidden="true">
          ▾
        </span>
      </button>

      {/* See FacetMenu: on a phone this panel is a bottom sheet, and a sheet
          needs a scrim and a way out. */}
      <span className="jobs-sheet-scrim" aria-hidden="true" onMouseDown={close} />
      <div className="jobs-where-panel" role="menu">
        <button type="button" className="jobs-sheet-done" onClick={close}>
          {t("filterDone")}
        </button>
        {detected && zone ? (
          <p className="jobs-where-detected">
            {PIN}
            {t("whereDetected")} — <b>{zone}</b>
            <Link
              className="jobs-where-swap"
              href={hrefFor({ country: null, city: null })}
              onClick={close}
            >
              {t("whereNotRight")}
            </Link>
          </p>
        ) : null}

        <h3>{t("whereCountry")}</h3>
        <div className="jobs-where-countries">
          {countries.map((option) => (
            <Link
              key={option.value}
              href={hrefFor({ country: option.value, city: null })}
              className={`jobs-opt${option.value === country ? " is-on" : ""}`}
              role="menuitem"
              onClick={close}
            >
              <span>{countryLabel(option.value)}</span>
              <span className="jobs-opt-n">{option.count.toLocaleString()}</span>
            </Link>
          ))}
        </div>

        {country ? (
          <div className="jobs-where-cities">
            <h3>{t("whereCities", { country: countryLabel(country) })}</h3>
            {cities === null ? (
              <p className="jobs-where-hint">{t("whereLoading")}</p>
            ) : cities.length === 0 ? (
              // Singapore and Hong Kong land here by design: this vocabulary
              // has no subdivision below the city-state, so the honest answer
              // is "no breakdown", not an invented district.
              <p className="jobs-where-hint">
                {t("whereNoCities", { country: countryLabel(country) })}
              </p>
            ) : (
              <div className="jobs-where-pills">
                {cities.map((option) => (
                  <Link
                    key={option.value}
                    href={hrefFor({
                      country,
                      city: option.value === city ? null : option.value,
                    })}
                    className={`jobs-pill${option.value === city ? " is-on" : ""}`}
                    onClick={close}
                  >
                    {option.value}
                    <span className="jobs-opt-n">{option.count.toLocaleString()}</span>
                  </Link>
                ))}
              </div>
            )}
          </div>
        ) : null}

        <div className="jobs-where-foot">
          <Link href={hrefFor({ country: null, city: null })} onClick={close}>
            {t("whereAnywhere")}
          </Link>
        </div>
      </div>
    </span>
  );
}
