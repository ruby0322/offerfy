import assert from "node:assert/strict";
import test from "node:test";
import type { Application } from "@/lib/applications";
import type { Digest, DigestItem } from "@/lib/digest";
import type { ResumeHealth } from "@/lib/dashboard";
import { nextSteps, type NextStepInput } from "./nextSteps";

const NOW = new Date("2026-11-15T12:00:00Z");

function ago(days: number): string {
  return new Date(NOW.getTime() - days * 86_400_000).toISOString();
}

function row(over: Partial<Application> = {}): Application {
  return {
    job_id: "j1",
    job_title: "Engineer",
    company: "Acme",
    apply_url: "https://example.com",
    job_is_active: true,
    status: "saved",
    source: "search",
    tailor_resume_id: null,
    applied_at: null,
    applied_confirmed_at: null,
    replied_at: null,
    last_status_at: ago(1),
    notes: null,
    ...over,
  };
}

const HEALTHY: ResumeHealth = {
  has_master: true,
  published: true,
  facts_state: "fresh",
  matching_revision_current: true,
  needs_rebuild: false,
};

function digestWith(items: Partial<DigestItem>[]): Digest {
  return {
    status: "full",
    built_at: ago(3),
    opened_at: null,
    candidate_count: 40,
    items: items.map((it, i) => ({
      job_id: `d${i}`,
      job_title: "ML Engineer",
      company: "Appier",
      apply_url: "https://example.com",
      job_is_active: true,
      track: "strong_fit",
      rank: i,
      tier: "Strong fit",
      fit_score: 0.9,
      reason: null,
      gap: null,
      ...it,
    })),
  };
}

function input(over: Partial<NextStepInput> = {}): NextStepInput {
  return {
    applications: [],
    health: HEALTHY,
    digest: null,
    hasPreference: true,
    cardOnPage: false,
    masterId: "r1",
    ...over,
  };
}

test("nothing to do produces an empty list, not a filler task", () => {
  assert.deepEqual(nextSteps(input(), NOW), []);
});

test("a blocker outranks any amount of pending work", () => {
  // While matching cannot read the resume, every number under it is partly
  // fiction, so clearing that beats sending anything.
  const steps = nextSteps(
    input({
      health: { ...HEALTHY, published: false },
      applications: [
        row({ status: "tailored", last_status_at: ago(9) }),
        row({ job_id: "j2", status: "applied", applied_at: ago(30), last_status_at: ago(30) }),
      ],
    }),
    NOW,
  );
  assert.equal(steps[0].kind, "publish");
  assert.deepEqual(steps.slice(1).map((s) => s.kind), ["send", "followUp"]);
});

test("no resume at all leads the ladder, ahead of the preference", () => {
  // The hole this closes: `publish` only fires once a master exists, so a
  // brand new account was told to state a preference while the document the
  // preference filters had not been written yet.
  const steps = nextSteps(
    input({
      health: { ...HEALTHY, has_master: false, published: false },
      hasPreference: false,
      masterId: null,
    }),
    NOW,
  );
  assert.deepEqual(steps.map((s) => s.kind), ["create", "preferences"]);
  // The chooser, so the band does not pick upload or blank for them.
  assert.equal(steps[0].href, "/new");
});

test("create and publish are never offered together", () => {
  // They are the same obligation at two stages; an unpublished master is
  // past the first one.
  const none = nextSteps(
    input({ health: { ...HEALTHY, has_master: false, published: false }, masterId: null }),
    NOW,
  );
  assert.deepEqual(none.map((s) => s.kind), ["create"]);
  const unpublished = nextSteps(input({ health: { ...HEALTHY, published: false } }), NOW);
  assert.deepEqual(unpublished.map((s) => s.kind), ["publish"]);
});

test("a missing preference is a blocker too", () => {
  const steps = nextSteps(input({ hasPreference: false }), NOW);
  assert.deepEqual(steps.map((s) => s.kind), ["preferences"]);
  assert.equal(steps[0].href, "/preferences");
});

test("the preferences step yields to the card once a seed exists", () => {
  // With the onboarding card on the page, "say what you want" is the card
  // itself. A band whose button navigates away to the full editor would be
  // telling the person to leave the three pre-answered questions for the
  // wall of unticked ones the card replaced.
  const steps = nextSteps(input({ hasPreference: false, cardOnPage: true }), NOW);
  assert.deepEqual(steps, []);
  // Blockers above it still lead: the card needs a resume to have a seed.
  const noResume = nextSteps(
    input({
      health: { ...HEALTHY, has_master: false, published: false },
      hasPreference: false,
      cardOnPage: true,
      masterId: null,
    }),
    NOW,
  );
  assert.deepEqual(noResume.map((s) => s.kind), ["create"]);
});

test("a rebuild is only offered once the resume is published", () => {
  // Rebuilding reads a published revision; offering it before there is one
  // would be a button that cannot work.
  const unpublished = nextSteps(
    input({ health: { ...HEALTHY, published: false, needs_rebuild: true } }),
    NOW,
  );
  assert.deepEqual(unpublished.map((s) => s.kind), ["publish"]);
  const published = nextSteps(
    input({ health: { ...HEALTHY, needs_rebuild: true } }),
    NOW,
  );
  assert.deepEqual(published.map((s) => s.kind), ["rebuild"]);
  // It spends credits, so it must never be a plain link.
  assert.equal(published[0].href, null);
});

test("the ladder runs send, then follow up, then confirm", () => {
  const steps = nextSteps(
    input({
      applications: [
        row({ job_id: "confirm", status: "applied", applied_at: ago(3), applied_confirmed_at: null, last_status_at: ago(3) }),
        row({ job_id: "quiet", status: "applied", applied_at: ago(20), applied_confirmed_at: ago(20), last_status_at: ago(20) }),
        row({ job_id: "ready", status: "tailored", last_status_at: ago(6) }),
      ],
    }),
    NOW,
  );
  assert.deepEqual(steps.map((s) => s.kind), ["send", "followUp", "confirm"]);
  assert.deepEqual(steps.map((s) => s.jobId), ["ready", "quiet", "confirm"]);
});

test("the most neglected row leads its own group", () => {
  const steps = nextSteps(
    input({
      applications: [
        row({ job_id: "newer", status: "tailored", last_status_at: ago(2) }),
        row({ job_id: "older", status: "tailored", last_status_at: ago(11) }),
      ],
    }),
    NOW,
  );
  assert.deepEqual(steps.map((s) => s.jobId), ["older", "newer"]);
  assert.equal(steps[0].params.days, 11);
});

test("a quiet row is not also asked to be confirmed", () => {
  // It would otherwise appear twice: once to chase, once to confirm. Chasing
  // is the more useful of the two, and the confirm rides along with it.
  const steps = nextSteps(
    input({
      applications: [
        row({ status: "applied", applied_at: ago(30), applied_confirmed_at: null, last_status_at: ago(30) }),
      ],
    }),
    NOW,
  );
  assert.deepEqual(steps.map((s) => s.kind), ["followUp"]);
});

test("the week's best fit is offered last, and only if untracked", () => {
  const untracked = nextSteps(input({ digest: digestWith([{ job_id: "fresh" }]) }), NOW);
  assert.deepEqual(untracked.map((s) => s.kind), ["tailor"]);
  assert.equal(untracked[0].params.score, "90");

  // Already in the pipeline: suggesting it again would be telling someone to
  // start work they have started.
  const tracked = nextSteps(
    input({
      digest: digestWith([{ job_id: "fresh" }]),
      applications: [row({ job_id: "fresh", status: "tailored", last_status_at: ago(1) })],
    }),
    NOW,
  );
  assert.deepEqual(tracked.map((s) => s.kind), ["send"]);
});

test("a closed posting is never suggested", () => {
  const steps = nextSteps(
    input({ digest: digestWith([{ job_id: "gone", job_is_active: false }]) }),
    NOW,
  );
  assert.deepEqual(steps, []);
});

test("the highest scoring fit wins, whatever order the digest arrived in", () => {
  const steps = nextSteps(
    input({
      digest: digestWith([
        { job_id: "weak", fit_score: 0.6, company: "Weak" },
        { job_id: "strong", fit_score: 0.95, company: "Strong" },
      ]),
    }),
    NOW,
  );
  assert.equal(steps[0].params.company, "Strong");
});
