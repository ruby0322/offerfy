#import "@preview/basic-resume:0.2.9": *

#let name = "Month Name Person"
#let email = "month.name@example.com"

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
  dates: dates-helper(start-date: "Aug 2023", end-date: "May 2027"),
  degree: "B.S.",
)

== Experience
#work(
  title: "Engineer",
  location: "City",
  company: "Co",
  dates: dates-helper(start-date: "Jun 2022", end-date: "Jul 2023"),
)
