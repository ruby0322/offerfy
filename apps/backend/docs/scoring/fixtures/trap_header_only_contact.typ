#let name = "Header Only Person"
#let email = "header.only@example.com"

#set page(
  paper: "a4",
  margin: (top: 8mm, rest: 18mm),
  header: align(center)[#text(size: 8pt)[Header Only Person | #email]],
)
#set text(font: "New Computer Modern", size: 10pt, lang: "en")

== Education
State University #h(1fr) 2018-09 -- 2022-06

== Experience
Engineer, Co #h(1fr) 2022-07 -- Present
- Built internal tools
