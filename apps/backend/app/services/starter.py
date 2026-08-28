from pathlib import Path

from app.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]
STARTER_PATH = BACKEND_ROOT / "typst" / "starter.typ"

PLACEHOLDERS = {
    "en": {
        "font": "New Computer Modern",
        "lang": "en",
        "name": "Your Name",
        "location": "City, Country",
        "email": "you@example.com",
        "education": "Education",
        "experience": "Experience",
        "institution": "University Name",
        "degree": "Degree, Field of Study",
        "job_title": "Job Title",
        "company": "Company Name",
        "bullet": "Replace this bullet with an achievement",
        "present": "Present",
    },
    "zh-TW": {
        "font": "Noto Serif CJK TC",
        "lang": "zh",
        "name": "你的姓名",
        "location": "城市, 國家",
        "email": "you@example.com",
        "education": "學歷",
        "experience": "工作經歷",
        "institution": "學校名稱",
        "degree": "學位, 科系",
        "job_title": "職稱",
        "company": "公司名稱",
        "bullet": "以具體成果取代此列點",
        "present": "至今",
    },
    "zh-CN": {
        "font": "Noto Serif CJK SC",
        "lang": "zh",
        "name": "你的姓名",
        "location": "城市, 国家",
        "email": "you@example.com",
        "education": "教育经历",
        "experience": "工作经历",
        "institution": "学校名称",
        "degree": "学位, 专业",
        "job_title": "职位",
        "company": "公司名称",
        "bullet": "用具体成果替换此条目",
        "present": "现在",
    },
}


def generate_starter(locale: str) -> str:
    if locale == "en":
        return STARTER_PATH.read_text(encoding="utf-8")
    if locale not in PLACEHOLDERS:
        locale = "zh-TW"
    return _render(PLACEHOLDERS[locale])


def _render(p: dict) -> str:
    return f'''#import "@preview/basic-resume:0.2.9": *

#let name = "{p["name"]}"
#let location = "{p["location"]}"
#let email = "{p["email"]}"
#let github = ""
#let linkedin = ""
#let phone = ""
#let personal-site = ""

#show: resume.with(
  author: name,
  location: location,
  email: email,
  accent-color: "#26428b",
  font: "{p["font"]}",
  paper: "a4",
  lang: "{p["lang"]}",
  author-position: left,
  personal-info-position: left,
)

== {p["education"]}

#edu(
  institution: "{p["institution"]}",
  location: "{p["location"]}",
  dates: dates-helper(start-date: "2019-09", end-date: "2023-06"),
  degree: "{p["degree"]}",
)

== {p["experience"]}

#work(
  title: "{p["job_title"]}",
  location: "{p["location"]}",
  company: "{p["company"]}",
  dates: dates-helper(start-date: "2023-07", end-date: "{p["present"]}"),
)
- {p["bullet"]}
'''


def default_title(locale: str) -> str:
    if locale == "zh-CN":
        return "未命名简历"
    if locale == "zh-TW":
        return "未命名履歷"
    return "Untitled"


def title_from_filename(filename: str | None, locale: str) -> str:
    stem = Path(filename or "").name.strip()
    stem = Path(stem).stem.strip()
    if not stem:
        return default_title(locale)
    return stem[:255]


def resolve_package_path() -> str:
    settings = get_settings()
    if settings.typst_package_path:
        return settings.typst_package_path
    return str(BACKEND_ROOT / "typst" / "packages")


def resolve_font_paths() -> list[str]:
    settings = get_settings()
    if settings.typst_font_paths:
        return [p for p in settings.typst_font_paths.split(":") if p]
    fonts = BACKEND_ROOT / "fonts"
    return [str(fonts)] if fonts.is_dir() else []
