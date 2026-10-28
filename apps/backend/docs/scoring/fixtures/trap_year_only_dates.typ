#import "@preview/basic-resume:0.2.9": *

#let name = "Year Only Person"
#let location = "City, Country"
#let email = "year.only@example.com"

#show: resume.with(
  author: name,
  location: location,
  email: email,
  accent-color: "#f4be82",
  font: "New Computer Modern",
  paper: "a4",
  lang: "en",
  author-position: left,
  personal-info-position: left,
)

== Education

#edu(
  institution: "State University",
  location: "City, Country",
  dates: dates-helper(start-date: "2018", end-date: "2022"),
  degree: "B.S. Computer Science",
)

== Experience

#work(
  title: "Software Engineer",
  location: "City, Country",
  company: "Example Labs",
  dates: dates-helper(start-date: "2022", end-date: "Present"),
)
- Shipped product features with measurable impact
