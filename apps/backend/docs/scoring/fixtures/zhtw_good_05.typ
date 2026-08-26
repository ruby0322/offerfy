#import "@preview/basic-resume:0.2.9": *

#let name = "鄭佩玲"
#let location = "台北, 臺灣"
#let email = "peiling.cheng@example.com"

#show: resume.with(
  author: name,
  location: location,
  email: email,
  accent-color: "#f4be82",
  font: "Noto Serif CJK TC",
  paper: "a4",
  lang: "zh",
  author-position: left,
  personal-info-position: left,
)

== 學歷

#edu(
  institution: "國立清華大學",
  location: "台北, 臺灣",
  dates: dates-helper(start-date: "2018-09", end-date: "2022-06"),
  degree: "資訊工程學士",
)

== 工作經歷

#work(
  title: "軟體工程師",
  location: "台北, 臺灣",
  company: "北風數據",
  dates: dates-helper(start-date: "2022-07", end-date: "至今"),
)
- Shipped product features with measurable impact
