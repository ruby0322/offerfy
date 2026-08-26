#import "@preview/basic-resume:0.2.9": *

#let name = "No Headings Person"
#let email = "no.headings@example.com"

#show: resume.with(
  author: name,
  email: email,
  accent-color: "#f4be82",
  font: "New Computer Modern",
  paper: "a4",
  lang: "en",
)

#edu(
  institution: "State University",
  location: "City",
  dates: dates-helper(start-date: "2018-09", end-date: "2022-06"),
  degree: "B.S.",
)
#work(
  title: "Engineer",
  location: "City",
  company: "Co",
  dates: dates-helper(start-date: "2022-07", end-date: "Present"),
)
