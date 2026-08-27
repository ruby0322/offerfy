"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { useTranslations } from "next-intl";

const BULLET_BEFORE = "- worked on the resume editor";
const BULLET_AFTER =
  "- built a Typst resume editor with live PDF preview and chat that edits the same file";
const BULLET_ATS =
  "- added ATS checks on the compiled PDF — parseability, not hireability";

const SOURCE_HEAD = `== Experience

#work(
  title: "Software Engineer",
  company: "Offerfy",
  dates: dates-helper(start-date: "Jan 2025", end-date: "Present"),
)`;

type Beat = 0 | 1 | 2;

function subscribeReducedMotion(onStoreChange: () => void) {
  const media = window.matchMedia("(prefers-reduced-motion: reduce)");
  media.addEventListener("change", onStoreChange);
  return () => media.removeEventListener("change", onStoreChange);
}

function reducedMotionSnapshot() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function stripBullet(line: string): string {
  return line.replace(/^-\s*/, "");
}

export default function EditorMock() {
  const tNav = useTranslations("nav");
  const tEditor = useTranslations("editor");
  const tMock = useTranslations("landing.mock");
  const reducedMotion = useSyncExternalStore(
    subscribeReducedMotion,
    reducedMotionSnapshot,
    () => false,
  );
  const [beat, setBeat] = useState<Beat>(0);

  useEffect(() => {
    if (reducedMotion) {
      return;
    }
    const id = window.setInterval(() => {
      setBeat((current) => ((current + 1) % 3) as Beat);
    }, 2500);
    return () => window.clearInterval(id);
  }, [reducedMotion]);

  const shownBeat: Beat = reducedMotion ? 2 : beat;
  const chatActive = shownBeat === 0;
  const firstPreview = shownBeat === 2 ? BULLET_AFTER : BULLET_BEFORE;

  return (
    <div className="landing-mock" aria-hidden="true" suppressHydrationWarning>
      <div className="landing-mock-header">
        <span className="landing-mock-brand">{tNav("brand")}</span>
        <span className="landing-mock-doc">{tMock("docTitle")}</span>
      </div>
      <div className="landing-mock-tabs">
        <span className={chatActive ? undefined : "is-active"}>{tEditor("tabTypst")}</span>
        <span className={chatActive ? "is-active" : undefined}>{tEditor("tabChat")}</span>
        <span>{tEditor("tabTemplate")}</span>
      </div>
      <div className="landing-mock-body">
        <div className="landing-mock-left">
          {chatActive ? (
            <>
              <p className="landing-mock-prompt">{tMock("chatPrompt")}</p>
              <div className="chat-diff">
                <div className="chat-diff-line chat-diff-del">
                  <span className="chat-diff-gutter">-</span>
                  <span className="chat-diff-text">{stripBullet(BULLET_BEFORE)}</span>
                </div>
                <div className="chat-diff-line chat-diff-add">
                  <span className="chat-diff-gutter">+</span>
                  <span className="chat-diff-text">{stripBullet(BULLET_AFTER)}</span>
                </div>
              </div>
            </>
          ) : (
            <pre className="landing-mock-source">
              {SOURCE_HEAD}
              {"\n"}
              <span className={shownBeat === 1 ? "landing-mock-hl" : undefined}>{BULLET_AFTER}</span>
              {"\n"}
              {BULLET_ATS}
            </pre>
          )}
        </div>
        <div className="landing-mock-preview">
          <p className="landing-mock-section">{tMock("experience")}</p>
          <div className="landing-mock-job">
            <strong>Software Engineer</strong>
            <span>Jan 2025 — Present</span>
          </div>
          <p className="landing-mock-company">Offerfy</p>
          <ul>
            <li>{stripBullet(firstPreview)}</li>
            <li>{stripBullet(BULLET_ATS)}</li>
          </ul>
        </div>
      </div>
    </div>
  );
}
