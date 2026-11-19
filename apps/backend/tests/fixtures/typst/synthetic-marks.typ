#import "@preview/basic-resume:0.2.9": *

#show: resume.with(
  author: "Blake Marsh",
  email: "blake.marsh@example.com",
  accent-color: "#1f5f4a",
  paper: "us-letter",
  lang: "en",
)

== Experience

#work(
  title: "Platform Engineer",
  location: "Remote",
  company: "Fabrikam Example Inc.",
  dates: "2021 -- 2024",
)
- Shipped *zero-downtime* deploys and kept the _error budget_ intact.
- Wrote the #link("https://example.com/runbook")[runbook] the on-call team still uses.
- Maintained *#link("https://example.org/docs")[the public docs]* as a single source of truth.
// - Retired the legacy cron host (cut from the final draft).
- Owned the [nested _content_ block] of the rollout checklist.
- Escapes in one line: R\&D, ops\@night, 5\* rating, \~100 hosts, \$2M budget.

== Core *Skills* and #link("https://example.com/stack")[stack]

- *Tooling:* Terraform, Kubernetes, #link("https://example.com/ci")[CI pipelines]
- _Languages:_ Go, Python
