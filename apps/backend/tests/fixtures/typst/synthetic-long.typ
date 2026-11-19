#import "@preview/basic-resume:0.2.9": *

#let name = "Avery Quill"
#let phone = "+1 555 0100"
#let mail = "avery.quill@example.com"

#show: resume.with(
  author: name,
  author-position: center,
  location: "Springfield, Exampleland",
  email: mail,
  github: "github.com/example-avery",
  personal-site: "avery.example.org",
  accent-color: "#26428b",
  font: ("New Computer Modern", "Libertinus Serif"),
  paper: "a4",
  lang: "en",
)

#set par(leading: 0.6em)

== Summary

#align(left)[Backend engineer with a taste for small, boring systems that keep working. Writes the runbook before the feature.]

== Experience

#work(
  title: "Senior Engineer",
  location: "Remote",
  company: "Northwind Example Co.",
  dates: dates-helper(start-date: "Jan 2022", end-date: "Present"),
)
- Rebuilt the nightly reconciliation job so that every ledger, queue and cache agreed on one answer, then wrote the runbook, the alerts and the rollback plan for it; Rebuilt the nightly reconciliation job so that every ledger, queue and cache agreed on one answer, then wrote the runbook, the alerts and the rollback plan for it; Rebuilt the nightly reconciliation job so that every ledger, queue and cache agreed on one answer, then wrote the runbook, the alerts and the rollback plan for it; Rebuilt the nightly reconciliation job so that every ledger, queue and cache agreed on one answer, then wrote the runbook, the alerts and the rollback plan for it; Rebuilt the nightly reconciliation job so that every ledger, queue and cache agreed on one answer, then wrote the runbook, the alerts and the rollback plan for it; Rebuilt the nightly reconciliation job so that every ledger, queue and cache agreed on one answer, then wrote the runbook, the alerts and the rollback plan for it; Rebuilt the nightly reconciliation job so that every ledger, queue and cache agreed on one answer, then wrote the runbook, the alerts and the rollback plan for it; Rebuilt the nightly reconciliation job so that every ledger, queue and cache agreed on one answer, then wrote the runbook, the alerts and the rollback plan for it; Rebuilt the nightly reconciliation job so that every ledger, queue and cache agreed on one answer, then wrote the runbook, the alerts and the rollback plan for it; Rebuilt the nightly reconciliation job so that every ledger, queue and cache agreed on one answer, then wrote the runbook, the alerts and the rollback plan for it; Rebuilt the nightly reconciliation job so that every ledger, queue and cache agreed on one answer, then wrote the runbook, the alerts and the rollback plan for it; Rebuilt the nightly reconciliation job so that every ledger, queue and cache agreed on one answer, then wrote the runbook, the alerts and the rollback plan for it; Rebuilt the nightly reconciliation.
- Led a team of four through a zero-downtime database migration.

#work(
  title: "Software Engineer",
  location: "Springfield",
  company: "Contoso Sample Ltd.",
  dates: "",
)
- Built the internal deploy dashboard used by 30 engineers.

#pagebreak()

== Education

#edu(
  institution: "Example State University",
  location: "Springfield, Exampleland",
  degree: "B.S. in Computer Science",
  dates: dates-helper(start-date: "Sep 2014", end-date: "Jun 2018"),
)
- Graduated with honors; teaching assistant for Algorithms.

== Projects

#project(
  role: "Maintainer",
  name: "tinyqueue",
  url: "example.com/tinyqueue",
  dates: dates-helper(start-date: "2020", end-date: "Present"),
)
- A job queue small enough to read in one sitting.

== Skills & Awards

- *Languages:* Python, Go, SQL
- *Awards:* Example Hackathon, first place, 2021
