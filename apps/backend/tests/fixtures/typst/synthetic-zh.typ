#import "@preview/basic-resume:0.2.9": *

#let name = "林小筑"
#let phone = "0900-000-000"
#let mail = "xiaozhu.lin@example.com"

#show: resume.with(
  author: name,
  author-position: center,
  location: "臺北市，範例區",
  email: mail,
  github: "github.com/example-xiaozhu",
  personal-site: "xiaozhu.example.org",
  accent-color: "#8b2635",
  font: ("New Computer Modern", "Noto Serif CJK TC"),
  paper: "a4",
  lang: "zh",
)

#set par(leading: 0.7em)

== 簡介

#align(left)[後端工程師，偏好小而穩的系統，寫功能之前先寫維運手冊。]

== 工作經歷

#work(
  title: "資深工程師",
  location: "遠端",
  company: "範例北風股份有限公司",
  dates: dates-helper(start-date: "2022 年 1 月", end-date: "至今"),
)
- 將訂單服務的 p99 延遲從 900 毫秒降到 240 毫秒。
- 帶領四人團隊完成零停機的資料庫遷移。

#work(
  title: "軟體工程師",
  location: "臺北",
  company: "範例樣本有限公司",
  dates: "",
)
- 建立內部部署儀表板，三十位工程師每天使用。

#pagebreak()

== 學歷

#edu(
  institution: "範例大學",
  location: "臺北市，範例區",
  degree: "資訊工程學系 學士",
  dates: dates-helper(start-date: "2014 年 9 月", end-date: "2018 年 6 月"),
)
- 以優異成績畢業；曾擔任演算法課程助教。

== 專案

#project(
  role: "維護者",
  name: "tinyqueue",
  url: "example.com/tinyqueue",
  dates: dates-helper(start-date: "2020", end-date: "至今"),
)
- 一個小到可以一次讀完的工作佇列。

== 技能 & 獎項

- *語言：* Python、Go、SQL
- *獎項：* 範例黑客松 第一名，2021
