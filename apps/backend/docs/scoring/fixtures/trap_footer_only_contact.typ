#let name = "Footer Only Person"
#let email = "footer.only@example.com"

#set page(
  paper: "a4",
  margin: (bottom: 18mm, rest: 18mm),
  footer: align(center)[#text(size: 8pt)[Footer Only Person | #email]],
)
#set text(font: "New Computer Modern", size: 10pt, lang: "en")

== Education
State University #h(1fr) 2018-09 -- 2022-06

== Experience
Engineer, Co #h(1fr) 2022-07 -- Present
- Built internal tools
