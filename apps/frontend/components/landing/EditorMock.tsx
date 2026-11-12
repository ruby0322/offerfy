"use client";

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useTranslations } from "next-intl";
import {
  BULLET_AFTER,
  BULLET_ATS,
  BULLET_BEFORE,
  SOURCE_HEAD,
  SOURCE_LINE,
  buildScript,
  frameAt,
  sameFrame,
  scriptDuration,
  streamTokens,
  type MockFrame,
} from "./mock-script";

function subscribeReducedMotion(onStoreChange: () => void) {
  const media = window.matchMedia("(prefers-reduced-motion: reduce)");
  media.addEventListener("change", onStoreChange);
  return () => media.removeEventListener("change", onStoreChange);
}

function reducedMotionSnapshot() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * The hero's editor mockup, playing the loop described in `mock-script.ts`.
 *
 * This component decides nothing about the animation: it reads a frame and
 * draws it. The frame at 0ms is the finished state, which is what the server
 * renders and what a reduced-motion viewer keeps, so the page makes its point
 * before and without any movement.
 */
export default function EditorMock() {
  const tNav = useTranslations("nav");
  const tEditor = useTranslations("editor");
  const tMock = useTranslations("landing.mock");

  const prompt = tMock("chatPrompt");
  const reply = tMock("chatReply");
  const stream = useMemo(() => streamTokens(reply), [reply]);
  const len = useMemo(
    () => ({
      promptChars: prompt.length,
      replyTokens: stream.tokens.length,
      lineChars: SOURCE_LINE.length,
    }),
    [prompt, stream],
  );
  const total = useMemo(() => scriptDuration(buildScript(len)), [len]);

  const reducedMotion = useSyncExternalStore(
    subscribeReducedMotion,
    reducedMotionSnapshot,
    () => false,
  );
  const [liveFrame, setFrame] = useState<MockFrame>(() => frameAt(0, len));
  const [onScreen, setOnScreen] = useState(false);
  const [tabHidden, setTabHidden] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const elapsedRef = useRef(0);

  // Off-screen is the common case once anyone scrolls, and the old mock kept
  // its interval running for the life of the page either way.
  useEffect(() => {
    const node = rootRef.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      (entries) => setOnScreen(entries.some((entry) => entry.isIntersecting)),
      { threshold: 0.25 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const sync = () => setTabHidden(document.visibilityState === "hidden");
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => document.removeEventListener("visibilitychange", sync);
  }, []);

  // Reduced motion gets the poster — the same frame the server rendered.
  // Derived here rather than pushed back through an effect, so turning the
  // preference on cannot cascade a second render.
  const poster = useMemo(() => frameAt(0, len), [len]);
  const frame = reducedMotion ? poster : liveFrame;

  const running = onScreen && !tabHidden && !reducedMotion;

  useEffect(() => {
    if (!running) return;
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      // Clamped so returning to a backgrounded tab resumes where it paused
      // instead of jumping several loops ahead on one enormous delta.
      const delta = Math.min(now - last, 250);
      last = now;
      elapsedRef.current = (elapsedRef.current + delta) % total;
      const next = frameAt(elapsedRef.current, len);
      // Most frames are identical to the last one; returning `prev` lets React
      // bail out instead of re-rendering at 60fps.
      setFrame((prev) => (sameFrame(prev, next) ? prev : next));
      raf = window.requestAnimationFrame(tick);
    };
    raf = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(raf);
  }, [running, total, len]);

  const chatOpen = frame.tab === "chat";
  const typed = prompt.slice(0, frame.promptChars);
  const streamed = stream.tokens.slice(0, frame.replyTokens).join(stream.sep);
  const written = SOURCE_LINE.slice(0, frame.lineChars);

  return (
    <div className="landing-mock" aria-hidden="true" ref={rootRef}>
      <div className="landing-mock-header">
        <span className="landing-mock-brand">{tNav("brand")}</span>
        <span className="landing-mock-doc">{tMock("docTitle")}</span>
      </div>
      <div className="landing-mock-body">
        <div className="landing-mock-left">
          {/* The tabs belong to this panel and the preview is named over its
              own, the way the editor itself splits them: there the preview
              keeps a pane in both layouts and is never one of the tabs. */}
          <div className="landing-mock-bar landing-mock-tabs">
            <span className={chatOpen ? undefined : "is-active"}>{tEditor("tabTypst")}</span>
            <span className={chatOpen ? "is-active" : undefined}>{tEditor("tabChat")}</span>
            <span>{tEditor("tabTemplate")}</span>
          </div>
          <div className="landing-mock-pane">
          {chatOpen ? (
            <div className="landing-mock-chat">
              <p
                className="landing-mock-prompt"
                data-composing={frame.composing ? "true" : "false"}
              >
                {typed}
                {frame.composing ? <span className="landing-mock-caret" /> : null}
              </p>
              {frame.phase === "send" ? (
                <p className="landing-mock-thinking">
                  <span className="landing-mock-dot" />
                  {tEditor("thinking")}
                </p>
              ) : (
                <p className="landing-mock-reply">{streamed}</p>
              )}
              <div className="landing-mock-diffbar" data-shown={frame.diffBar ? "true" : "false"}>
                <span className="landing-mock-chev" data-open={frame.diffOpen ? "true" : "false"} />
                <span className="landing-mock-add">+1</span>
                <span className="landing-mock-del">−1</span>
              </div>
              <div className="landing-mock-difflines" data-open={frame.diffOpen ? "true" : "false"}>
                <div className="chat-diff">
                  <div className="chat-diff-line chat-diff-del">
                    <span className="chat-diff-gutter">-</span>
                    <span className="chat-diff-text">{BULLET_BEFORE}</span>
                  </div>
                  <div className="chat-diff-line chat-diff-add">
                    <span className="chat-diff-gutter">+</span>
                    <span className="chat-diff-text">{BULLET_AFTER}</span>
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <pre className="landing-mock-source">
              {SOURCE_HEAD}
              {"\n"}
              <span className="landing-mock-line" data-lit={frame.sweep ? "true" : "false"}>
                {written}
              </span>
              {"\n"}
              {`- ${BULLET_ATS}`}
            </pre>
          )}
          </div>
        </div>
        <div className="landing-mock-preview">
          <div className="landing-mock-bar landing-mock-previewbar">
            <span className="landing-mock-panename">{tEditor("preview")}</span>
            <p className="landing-mock-compiling" data-shown={frame.compiling ? "true" : "false"}>
              {tMock("compiling")}
            </p>
          </div>
          <div className="landing-mock-pane">
          <p className="landing-mock-section">{tMock("experience")}</p>
          <div className="landing-mock-job">
            <strong>Software Engineer</strong>
            <span>Jan 2025 — Present</span>
          </div>
          <p className="landing-mock-company">Offerfy</p>
          <ul>
            {/* Both bullets occupy one grid cell, so the cell is always as tall
                as the longer of the two and the swap cannot reflow the hero. */}
            <li className="landing-mock-swap">
              <span className="landing-mock-cell" data-shown={frame.updated ? "false" : "true"}>
                {BULLET_BEFORE}
              </span>
              <span className="landing-mock-cell" data-shown={frame.updated ? "true" : "false"}>
                {BULLET_AFTER}
              </span>
            </li>
            <li>{BULLET_ATS}</li>
          </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
