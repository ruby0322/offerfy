#import "@preview/basic-resume:0.2.9": *

#let name = "Column Person"
#let email = "column.person@example.com"

#set page(paper: "a4", margin: 12mm)
#set text(font: "New Computer Modern", size: 10pt, lang: "en")

#grid(
  columns: (1fr, 1fr),
  column-gutter: 18mm,
  [
    = Column Person
    #email

    == Education
    #edu(
      institution: "State University",
      location: "City",
      dates: dates-helper(start-date: "2018-09", end-date: "2022-06"),
      degree: "B.S.",
    )
    #lorem(70)

    == Experience
    #work(
      title: "Engineer",
      location: "City",
      company: "Co",
      dates: dates-helper(start-date: "2022-07", end-date: "Present"),
    )
    #lorem(70)
  ],
  [
    == Skills
    #lorem(90)
    #lorem(90)
  ],
)
