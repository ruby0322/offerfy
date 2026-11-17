import assert from "node:assert/strict";
import test from "node:test";
import type { Application } from "@/lib/applications";
import type { Resume } from "@/lib/api";
import { featuredResume, lastTouched, statsFor, tailorsOf } from "./resumeStats";

function master(id: string, updated: string): Resume {
  return { id, title: id, typst_source: "", kind: "master", updated_at: updated };
}

function tailor(id: string, parent: string, updated: string): Resume {
  return {
    id,
    title: id,
    typst_source: "",
    kind: "tailor",
    parent_resume_id: parent,
    updated_at: updated,
  };
}

function application(over: Partial<Application> = {}): Application {
  return {
    job_id: "j1",
    job_title: "Engineer",
    company: "Acme",
    apply_url: "https://example.com",
    job_is_active: true,
    status: "applied",
    source: "manual",
    tailor_resume_id: null,
    applied_at: "2026-11-10T12:00:00Z",
    applied_confirmed_at: null,
    replied_at: null,
    last_status_at: "2026-11-10T12:00:00Z",
    notes: null,
    ...over,
  };
}

test("a master's reach is what its tailors did, not what its own row did", () => {
  const resumes = [
    master("m", "2026-11-01T00:00:00Z"),
    tailor("t1", "m", "2026-11-02T00:00:00Z"),
    tailor("t2", "m", "2026-11-03T00:00:00Z"),
    // Another master's tailor: its sends must not land on m.
    master("other", "2026-11-04T00:00:00Z"),
    tailor("t3", "other", "2026-11-05T00:00:00Z"),
  ];
  const apps = [
    application({ tailor_resume_id: "t1" }),
    application({ tailor_resume_id: "t2" }),
    application({ tailor_resume_id: "t3" }),
    // Not tailored from anything: belongs to no family.
    application({ tailor_resume_id: null }),
  ];
  assert.deepEqual(
    tailorsOf(resumes[0], resumes).map((r) => r.id),
    ["t1", "t2"],
  );
  const stats = statsFor(resumes[0], resumes, apps);
  assert.equal(stats.tailors, 2);
  assert.equal(stats.sent, 2);
});

test("the reply line names the most recent answer in the family, and only that family", () => {
  const resumes = [
    master("m", "2026-11-01T00:00:00Z"),
    tailor("t1", "m", "2026-11-02T00:00:00Z"),
    tailor("t2", "m", "2026-11-03T00:00:00Z"),
    master("other", "2026-11-01T00:00:00Z"),
    tailor("t3", "other", "2026-11-02T00:00:00Z"),
  ];
  const apps = [
    application({ tailor_resume_id: "t1", company: "Older", replied_at: "2026-11-05T00:00:00Z" }),
    application({ tailor_resume_id: "t2", company: "Newer", replied_at: "2026-11-09T00:00:00Z" }),
    application({ tailor_resume_id: "t2", company: "Silent" }),
    application({ tailor_resume_id: "t3", company: "Stranger", replied_at: "2026-11-12T00:00:00Z" }),
  ];
  const stats = statsFor(resumes[0], resumes, apps);
  assert.equal(stats.sent, 3);
  assert.equal(stats.replied, 2);
  assert.equal(stats.reply?.company, "Newer");
});

test("a tailor counts only itself, so an orphan still reports honestly", () => {
  const resumes = [tailor("orphan", "gone", "2026-11-02T00:00:00Z")];
  const apps = [application({ tailor_resume_id: "orphan", replied_at: "2026-11-06T00:00:00Z" })];
  const stats = statsFor(resumes[0], resumes, apps);
  assert.equal(stats.tailors, 0);
  assert.equal(stats.sent, 1);
  assert.equal(stats.replied, 1);
});

test("the feature goes to the master that gets used, not the one touched last", () => {
  const resumes = [
    master("worked", "2026-11-01T00:00:00Z"),
    tailor("w1", "worked", "2026-11-01T00:00:00Z"),
    tailor("w2", "worked", "2026-11-01T00:00:00Z"),
    // Edited this morning and never sent: the old rule would have picked it.
    master("fresh", "2026-11-17T00:00:00Z"),
  ];
  const apps = [
    application({ tailor_resume_id: "w1" }),
    application({ tailor_resume_id: "w2" }),
  ];
  assert.equal(featuredResume(resumes, apps)?.id, "worked");
  // And the other rule still has a job: it is where "back to it" points.
  assert.equal(lastTouched(resumes)?.id, "fresh");
});

test("level on sends, the more recently edited master wins", () => {
  const resumes = [
    master("older", "2026-11-01T00:00:00Z"),
    master("newer", "2026-11-09T00:00:00Z"),
  ];
  assert.equal(featuredResume(resumes, [])?.id, "newer");
});

test("with no master at all the most recent document is featured", () => {
  const resumes = [
    tailor("a", "gone", "2026-11-02T00:00:00Z"),
    tailor("b", "gone", "2026-11-08T00:00:00Z"),
  ];
  assert.equal(featuredResume(resumes, [])?.id, "b");
  assert.equal(featuredResume([], []), null);
  assert.equal(lastTouched([]), null);
});
