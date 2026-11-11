import pytest

from app.services.jobs.enrich import function_of, load_taxonomy, seniority_of


@pytest.mark.parametrize("title,expected", [
    ("Staff Software Engineer, Infrastructure", "staff_plus"),
    ("Principal Engineer", "staff_plus"),
    ("Member of Technical Staff", "senior"),
    ("Senior Backend Engineer", "senior"),
    ("SDE II", "mid"),
    ("Software Engineer II", "mid"),
    ("Software Engineer", "mid"),
    ("New Grad Software Engineer", "new_grad"),
    ("Software Engineer, University Graduate", "new_grad"),
    ("Software Engineering Intern", "intern"),
    ("Junior Developer", "junior"),
    ("Engineering Manager, Billing", "manager"),
    ("資深後端工程師", "senior"),
    ("Wizard of Light Bulb Moments", "unknown"),
])
def test_seniority_of(title, expected):
    assert seniority_of(title) == expected


@pytest.mark.parametrize("title,description,expected", [
    ("Backend Engineer", "", "backend"),
    ("Frontend Engineer", "", "frontend"),
    ("Full Stack Engineer", "", "fullstack"),
    ("Machine Learning Engineer", "", "ml"),
    ("Research Scientist", "We train large language models.", "ml"),
    ("Data Engineer", "", "data"),
    ("Site Reliability Engineer", "", "infra"),
    ("Security Engineer", "", "security"),
    ("Silicon Design Engineer", "", "hardware"),
    ("Product Manager", "", "product"),
    ("Product Designer", "", "design"),
    ("後端工程師", "", "backend"),
    ("Software Engineer", "", "other"),
])
def test_function_of(title, description, expected):
    assert function_of(title, description) == expected


def test_function_falls_back_to_description_keywords():
    assert function_of("Engineer, Platform", "You will own our Kubernetes fleet.") == "infra"


@pytest.mark.parametrize("title,expected", [
    # " intern" (leading pad only) used to be a prefix match inside "internal".
    ("Internal Tools Engineer", "mid"),
    ("Internal Platform Engineer", "mid"),
    # "head of" (no padding at all) used to match inside "ahead of".
    ("Ahead of Curve Engineer", "mid"),
    # " fellow" (leading pad only) used to be a prefix match inside "fellowship".
    ("Research Fellowship Coordinator", "unknown"),
])
def test_seniority_of_does_not_match_inside_a_longer_word(title, expected):
    assert seniority_of(title) == expected


@pytest.mark.parametrize("title,description", [
    # "aws " (trailing pad only) used to match the suffix of "flaws ".
    ("Engineer, Platform", "This team debates design flaws in legacy systems all day."),
    # "ml engineer" (no padding) used to match inside "html engineer".
    ("HTML Engineer", ""),
    # "ai engineer" (no padding) used to match inside a city name ending in "ai".
    ("Shanghai Engineer", ""),
    # "ic design" (no padding) used to match inside "graphic design".
    ("Graphic Design Engineer", ""),
    # "asic" (no padding) used to match inside "basic".
    ("Basic Systems Administrator", ""),
    # "etl"/"elt" (no padding) used to match inside "settle"/"melt".
    ("Materials Engineer", "We monitor the silicon melt process closely."),
])
def test_function_of_does_not_match_inside_a_longer_word(title, description):
    assert function_of(title, description) == "other"


@pytest.mark.parametrize("title,description,expected", [
    # " etl " (both-sides padded, no punctuation variant) stopped matching
    # the moment the word sat at the end of a sentence or before a comma --
    # exactly how these acronyms show up in prose. Verbatim repro case.
    ("Engineer, Platform", "We maintain batch ETL.", "data"),
    # Same acronym, comma-adjacent instead of period-adjacent (the other
    # literal repro case; written as a full sentence rather than with a
    # leading ellipsis, since the ellipsis in the bug report stood for
    # elided context, not three literal periods glued to "ETL").
    ("Engineer, Platform", "We also handle ETL, plus reporting.", "data"),
    ("Engineer, Platform", "We built the ELT, then the dashboards.", "data"),
    ("Engineer, Platform", "Skills needed: SQL.", "data"),
    ("Engineer, Platform", "Must be comfortable with AWS.", "infra"),
    ("Engineer, Platform", "We use AWS, GCP, and Azure.", "infra"),
])
def test_function_of_matches_keyword_adjacent_to_punctuation(title, description, expected):
    assert function_of(title, description) == expected


@pytest.mark.parametrize("title,expected", [
    # This catalogue's own "Role, Qualifier" title idiom (see
    # "Engineering Manager, Billing" and "Staff Software Engineer,
    # Infrastructure" in the brief's table) puts a comma immediately after
    # a short acronym needle that was tightened to require a trailing
    # space -- that space is never there when a comma follows instead.
    ("CTO, Platform Engineering", "manager"),
    ("Lead, Platform Engineering", "senior"),
    ("SDE, Platform Team", "mid"),
])
def test_seniority_of_matches_acronym_before_a_title_comma(title, expected):
    assert seniority_of(title) == expected


@pytest.mark.parametrize("title,expected", [
    ("LLM, Applied Research Engineer", "ml"),
    ("ASIC, Physical Design Engineer", "hardware"),
    ("TPM, Platform Reliability", "product"),
])
def test_function_of_matches_acronym_before_a_title_comma(title, expected):
    assert function_of(title, "") == expected


def test_taxonomy_file_covers_both_vocabularies_exactly():
    taxonomy = load_taxonomy()
    assert {rule.seniority for rule in taxonomy.seniority_rules} <= {
        "intern", "new_grad", "junior", "mid", "senior", "staff_plus", "manager",
    }
    assert {rule.function for rule in taxonomy.function_rules} <= {
        "backend", "frontend", "fullstack", "ml", "data", "infra",
        "security", "hardware", "product", "design",
    }
