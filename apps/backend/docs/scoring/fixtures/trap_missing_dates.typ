#import "@preview/basic-resume:0.2.9": *

#let name = "No Dates Person"
#let email = "no.dates@example.com"

#show: resume.with(
  author: name,
  email: email,
  accent-color: "#f4be82",
  font: "New Computer Modern",
  paper: "a4",
  lang: "en",
)

== Education
#edu(
  institution: "State University",
  location: "City",
  dates: "Summer 2023",
  degree: "B.S.",
)

== Experience
#work(
  title: "Engineer",
  location: "City",
  company: "Co",
  dates: "late 2022",
)
