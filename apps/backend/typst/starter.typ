#import "@preview/basic-resume:0.2.9": *

#let name = "Your Name"
#let location = "City, Country"
#let email = "you@example.com"
#let github = ""
#let linkedin = ""
#let phone = ""
#let personal-site = ""

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
  institution: "University Name",
  location: "City, Country",
  dates: dates-helper(start-date: "2019-09", end-date: "2023-06"),
  degree: "Degree, Field of Study",
)

== Experience

#work(
  title: "Job Title",
  location: "City, Country",
  company: "Company Name",
  dates: dates-helper(start-date: "2023-07", end-date: "Present"),
)
- Replace this bullet with an achievement
