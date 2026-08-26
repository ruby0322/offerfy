#import "@preview/basic-resume:0.2.9": *

#let name = "Hiro Tanaka"
#let location = "City, Country"
#let email = "hiro.tanaka@example.com"

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
  institution: "Kyoto University",
  location: "City, Country",
  dates: dates-helper(start-date: "2018-09", end-date: "2022-06"),
  degree: "B.S. Computer Science",
)

== Experience

#work(
  title: "Software Engineer",
  location: "City, Country",
  company: "Maple Robotics",
  dates: dates-helper(start-date: "2022-07", end-date: "Present"),
)
- Shipped product features with measurable impact
