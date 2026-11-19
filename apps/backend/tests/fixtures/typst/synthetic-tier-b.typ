#import "@preview/basic-resume:0.2.9": *

#show: resume.with(
  author: "Casey Brook",
  email: "casey.brook@example.com",
  accent-color: "#444444",
  lang: "en",
)

== Experience

#work(
  title: "Data Analyst",
  location: "Remote",
  company: "Initech Sample LLC",
  dates: "2019 -- 2023",
)
- Built weekly reporting that replaced three spreadsheets.
- Presented the results to finance#footnote[Quarterly review, 2022.] every quarter.

== Skills

#grid(
  columns: (1fr, 1fr),
  gutter: 8pt,
  [*Data*\ SQL, dbt, Airflow],
  [*Visualization*\ Superset, Matplotlib],
)

#let tag(label) = box(inset: 3pt, stroke: 0.5pt, radius: 2pt)[#label]
#tag("Open to relocation") #tag("Available in March")
