#import "@preview/basic-resume:0.2.9": *

#let name = "许雅惠"
#let location = "北京, 中国"
#let email = "yahui.xu@example.cn"

#show: resume.with(
  author: name,
  location: location,
  email: email,
  accent-color: "#f4be82",
  font: "Noto Serif CJK SC",
  paper: "a4",
  lang: "zh",
  author-position: left,
  personal-info-position: left,
)

== 教育经历

#edu(
  institution: "中国科学技术大学",
  location: "北京, 中国",
  dates: dates-helper(start-date: "2018-09", end-date: "2022-06"),
  degree: "计算机科学学士",
)

== 工作经历

#work(
  title: "软件工程师",
  location: "北京, 中国",
  company: "亮线顾问",
  dates: dates-helper(start-date: "2022-07", end-date: "现在"),
)
- Shipped product features with measurable impact
