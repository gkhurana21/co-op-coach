"""Shared text heuristics: is-this-an-internship, role category, co-op term, region, skills."""

import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

CATEGORIES = ["Software", "AI/ML/Data", "Hardware", "Quant", "Product/Design", "Business/Finance", "Other"]

INTERN_RE = re.compile(
    r"\b(intern|interns|internship|internships|co-?op|coop|stagiaire|summer analyst|summer associate|"
    r"student (?:developer|engineer|analyst|researcher|assistant|program)|work term|placement student)\b",
    re.I,
)
NOT_INTERN_RE = re.compile(r"\b(senior|staff|principal|manager|director|head of|lead)\b", re.I)


# campus part-time jobs (work-study, student assistant/worker) aren't internships; they're for the part-time feature
CAMPUS_JOB_RE = re.compile(r"work[- ]study|student (?:wage|worker|employee|assistant|ambassador|caller|staff)|"
                           r"\bstudent hourly\b|\btutor\b|\bgrader\b|\bteaching assistant\b", re.I)


def senior_title(title: str) -> bool:
    """'Senior Staff Engineer', 'Engineering Manager': a role for people with years of experience (LinkedIn and
    Indeed searches bring some in)."""
    return bool(NOT_INTERN_RE.search(title or "") and not INTERN_RE.search(title or ""))


def is_internship(title: str, extra: str = "") -> bool:
    if NOT_INTERN_RE.search(title) and not re.search(r"\bintern", title, re.I):
        return False
    if CAMPUS_JOB_RE.search(title) and not re.search(r"\bintern(?:ship)?\b|\bco-?op\b", title, re.I):
        return False
    return bool(INTERN_RE.search(title) or (extra and re.search(r"\b(intern|internship|co-?op)\b", extra, re.I)))


_CAT_RULES = [
    ("Quant", r"\b(quant(?!um)\w*|trad(er|ing)|market mak\w+|algorithmic)\b"),
    ("AI/ML/Data", r"\b(machine learning|ml|ai|artificial intelligence|data scien\w*|data analy\w*|data engineer\w*|"
                   r"analytics|deep learning|nlp|computer vision|research scientist|llm|business intelligence|bi developer)\b"),
    ("Hardware", r"\b(hardware|firmware|embedded|asic|fpga|electrical|silicon|circuit|rf|soc|vlsi|pcb|"
                 r"mechanical|robotics|mechatronics|power systems|semiconductor|validation engineer|verification)\b"),
    ("Product/Design", r"\b(product manag\w*|product design\w*|\bpm\b|ux|ui/ux|ui designer|user experience|"
                       r"designer|design intern|product owner|technical program)\b"),
    ("Software", r"\b(software|developer|swe|sde|backend|back-end|frontend|front-end|full[- ]?stack|devops|sre|"
                 r"site reliability|security|cyber\w*|cloud|mobile|ios|android|web|platform|infrastructure|"
                 r"qa|quality assurance|test automation|programmer|application|systems engineer|it\b|technology|"
                 r"engineering intern|engineer intern|developer intern)\b"),
    ("Business/Finance", r"\b(financ\w*|accounting|audit|bank\w*|investment|wealth|marketing|sales|operations|"
                         r"business|consult\w*|hr|human resources|actuar\w*|risk|treasury|tax|strategy|analyst|"
                         r"capital markets|insurance|underwrit\w*|communications|legal|supply chain|procurement)\b"),
]
_CAT_RES = [(c, re.compile(p, re.I)) for c, p in _CAT_RULES]

SIMPLIFY_CATEGORY = {
    "Software": "Software",
    "Software Engineering": "Software",
    "AI/ML/Data": "AI/ML/Data",
    "Data Science, AI & Machine Learning": "AI/ML/Data",
    "Hardware": "Hardware",
    "Hardware Engineering": "Hardware",
    "Product": "Product/Design",
    "Product Management": "Product/Design",
    "Quant": "Quant",
    "Quantitative Finance": "Quant",
}


# business functions that happen to contain a tech word ("Developer Marketing", "Sales Engineer... intern" excluded)
NONTECH_RE = re.compile(r"\b(marketing|sales|recruit\w*|talent|growth marketing|community|content|communications|"
                        r"social media|brand\w*|partnerships?|customer success|account (?:exec\w*|manag\w*)|"
                        r"business develop\w*|people ops|copywrit\w*|public relations|events? coordinator|"
                        r"business operations|commercial (?:ops|operations)|revenue operations|supply chain|legal|paralegal|law clerk)\b", re.I)
_TECH_CORE_RE = re.compile(r"\b(engineer\w*|software|swe|sde|programmer|data scien\w*|machine learning|ml engineer)\b",
                           re.I)


# engineering that isn't software, though "engineering intern" alone reads like it
_SOFTWARE_CORE_RE = re.compile(r"\b(software|developer|programmer|swe|sde|full[- ]?stack|back-?end|front-?end|devops|cyber\w*|web|"
                               r"cloud|machine learning|ml|ai|data scien\w*|data analy\w*|data engineer\w*|firmware|embedded|(?-i:IT))\b", re.I)
_HARDWARE_ENG_RE = re.compile(r"\b(manufactur\w*|\w*mechanical|industrial eng\w*|process eng\w*|materials? (?:eng\w*|scien\w*)|packaging|"
                              r"aerospace|aeronautic\w*|propulsion|structural|production eng\w*|tooling|thin films?|mems|photonics)\b", re.I)
_OTHER_ENG_RE = re.compile(r"\b(civil|chemical eng\w*|environmental|biomedical|bioprocess|geotechnical|traffic|transportation eng\w*|"
                           r"\w*construction|petroleum|mining eng\w*|nuclear|agricultur\w*|water resources|hvac|rail)\b", re.I)


_REFINE_ANY_RE = re.compile("|".join(r.pattern for r in (NONTECH_RE, _HARDWARE_ENG_RE, _OTHER_ENG_RE)), re.I)  # most titles: one look


def refine_category(title: str, category: str | None) -> str:
    """What a title says over the category stored with it: "Manufacturing Engineering Intern" isn't a Software role however the
    source labelled it, and "Business Operations, Analytics" isn't data science."""
    if not _REFINE_ANY_RE.search(title):
        return category or "Other"
    if NONTECH_RE.search(title) and not _TECH_CORE_RE.search(title):
        return "Business/Finance"
    if not _SOFTWARE_CORE_RE.search(title):
        if _OTHER_ENG_RE.search(title):
            return "Other"
        if _HARDWARE_ENG_RE.search(title):
            return "Hardware"
    return category or "Other"


def classify_category(title: str, description: str = "") -> str:
    known = refine_category(title, "?")
    if known != "?":
        return known
    for cat, rx in _CAT_RES:
        if rx.search(title):
            return cat
    if description:
        head = description[:1500]
        for cat, rx in _CAT_RES[:5]:
            if len(rx.findall(head)) >= 3:
                return cat
    return "Other"


# --- co-op terms -------------------------------------------------------------

_SEASON_RE = re.compile(r"\b(winter|summer|fall|autumn|spring|hiver|[ée]t[ée]|automne|printemps)\s*[-/']?\s*(20\d\d|\d\d)\b",
                        re.I)
_FRENCH_SEASON = {"hiver": "Winter", "été": "Summer", "ete": "Summer", "eté": "Summer", "éte": "Summer",
                  "automne": "Fall", "printemps": "Spring"}
_MONTH_TERM_RE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
    r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+(20\d\d)\b", re.I)
_MONTH_TO_SEASON = {"jan": "Winter", "feb": "Winter", "mar": "Winter", "apr": "Winter", "may": "Summer",
                    "jun": "Summer", "jul": "Summer", "aug": "Summer", "sep": "Fall", "oct": "Fall",
                    "nov": "Fall", "dec": "Fall"}
_DURATION_RE = re.compile(r"\b(4|8|12|16)[\s-]?(?:to[\s-]?(?:8|12|16)[\s-]?)?months?\b", re.I)


def extract_terms(text: str) -> list[str]:
    terms: list[str] = []
    for season, year in _SEASON_RE.findall(text):
        low = season.lower()
        season = "Fall" if low == "autumn" else _FRENCH_SEASON.get(low) or (
            "Summer" if low.replace("é", "e") == "ete" else season.capitalize())
        year = year if len(year) == 4 else "20" + year
        t = f"{season} {year}"
        if t not in terms:
            terms.append(t)
    if not terms:
        m = _MONTH_TERM_RE.search(text)
        if m:
            terms.append(f"{_MONTH_TO_SEASON[m.group(1)[:3].lower()]} {m.group(2)}")
    d = _DURATION_RE.search(text)
    if d:
        terms.append(f"{d.group(1)}-month")
    return terms


def normalize_terms(terms: list[str]) -> list[str]:
    out = []
    for t in terms or []:
        t = (t or "").strip()
        if not t or t.upper() == "N/A":
            continue
        t = re.sub(r"(?i)autumn", "Fall", t)
        if t not in out:
            out.append(t)
    return out


# --- regions -----------------------------------------------------------------

_PROVINCES = r"(ON|BC|QC|AB|MB|SK|NS|NB|NL|PE|YT|NT|NU)"
_CA_RE = re.compile(
    r"(canada|ontario|british columbia|qu[eé]bec|alberta|manitoba|saskatchewan|nova scotia|new brunswick|"
    r"toronto|waterloo|kitchener|vancouver|montr[eé]al|ottawa|calgary|edmonton|mississauga|markham|"
    r"burnaby|halifax|winnipeg|victoria, bc|hamilton|london, on|oakville|brampton|richmond hill|vaughan|"
    r"guelph|kanata|laval|regina|saskatoon|fredericton|st\. john's|surrey, bc|"
    rf",\s*{_PROVINCES}\b(?!-)|\b{_PROVINCES},\s*CA\b)",
    re.I,
)
_US_STATES = ("AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|"
              "NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC")
_US_RE = re.compile(
    rf"(united states|\busa\b|\bu\.s\.|,\s*({_US_STATES})\b|\b({_US_STATES}),\s*US\b|new york|san francisco|"
    r"seattle|boston|chicago|austin|los angeles|bay area|silicon valley|palo alto|mountain view|menlo park|"
    r"sunnyvale|san jose|cambridge, ma|washington, dc|atlanta|denver|pittsburgh|philadelphia|houston|dallas|miami)",
    re.I,
)
_US_STATE_NAMES = re.compile(
    r"\b(alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|idaho|"
    r"illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|"
    r"missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|north carolina|north dakota|ohio|"
    r"oklahoma|oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|"
    r"wisconsin|wyoming)\b", re.I)
_US_ABBR = re.compile(r"\b(NYC|SF|LA|DC|SFBA|US)\b|^US\b")
_REMOTE_RE = re.compile(r"\bremote\b", re.I)
_UNKNOWN_RE = re.compile(r"^\s*\d+\s+locations?\s*$|^\s*multiple locations\s*$", re.I)


def classify_regions(locations: list[str]) -> list[str]:
    regions: set[str] = set()
    for loc in locations or []:
        if not loc:
            continue
        if _REMOTE_RE.search(loc):
            regions.add("Remote")
            if re.search(r"canada", loc, re.I):
                regions.add("Canada")
            elif re.search(r"us|united states|america", loc, re.I):
                regions.add("US")
            continue
        if _UNKNOWN_RE.search(loc):
            regions.add("Unknown")
        elif _CA_RE.search(loc):
            regions.add("Canada")
        elif _US_RE.search(loc) or _US_STATE_NAMES.search(loc) or _US_ABBR.search(loc):
            regions.add("US")
        else:
            regions.add("Other")
    if len(regions) > 1:
        regions.discard("Unknown")
    return sorted(regions) or ["Unknown"]


# --- skills ------------------------------------------------------------------
# skill -> (regex, categories it signals)
_SK = {
    "Python": (r"\bpython\b", ["Software", "AI/ML/Data", "Quant"]),
    "Java": (r"\bjava\b(?!script)", ["Software"]),
    "C++": (r"\bc\+\+|\bcpp\b", ["Software", "Quant", "Hardware"]),
    "C": (r"(?<![\w#+/])C(?=\s*(?:/|,)\s*C\+\+)|\bC programming\b|\bANSI C\b|\bEmbedded C\b", ["Hardware", "Software"]),
    "C#": (r"\bc#|(?<![\w.-])\.net\b|\b(?:asp|vb|ado)\.net\b", ["Software"]),  # not careers@acme.net
    "JavaScript": (r"\bjavascript\b|\bjs\b|\becmascript\b", ["Software"]),
    "TypeScript": (r"\btypescript\b", ["Software"]),
    "Go": (r"\bgolang\b|\bGo(?=\s*(?:,|/|\)|and\b))", ["Software"]),
    "Rust": (r"\brust\b", ["Software"]),
    "Kotlin": (r"\bkotlin\b", ["Software"]),
    "Swift": (r"(?-i:\bSwift(?:UI)?\b)|\bswiftui\b", ["Software"]),  # not "a swift reply" or SWIFT payments
    "R": (r"(?<![\w-])R(?=\s*(?:,|/)\s*(?:python|sql|matlab|sas|stata))|\bR programming\b|\bRStudio\b", ["AI/ML/Data"]),
    "SQL": (r"\bsql\b|\bpostgres(?:ql)?\b|\bmysql\b|\bsqlite\b|\bt-sql\b", ["AI/ML/Data", "Software", "Business/Finance"]),
    "MATLAB": (r"\bmatlab\b|\bsimulink\b", ["Hardware", "AI/ML/Data"]),
    "Scala": (r"\bscala\b", ["Software", "AI/ML/Data"]),
    "Ruby": (r"\bruby\b|(?-i:\bRails\b)", ["Software"]),  # not "power rails" or "guard rails"
    "PHP": (r"\bphp\b|\blaravel\b", ["Software"]),
    "Bash": (r"\bbash\b|\bshell scripting\b|\bzsh\b", ["Software"]),
    "HTML/CSS": (r"\bhtml5?\b|\bcss3?\b|\btailwind\b|\bsass\b", ["Software"]),
    "Verilog/VHDL": (r"\bverilog\b|\bsystemverilog\b|\bvhdl\b", ["Hardware"]),
    # the language, not "PCB assembly" or an assembly line
    "Assembly": (r"\bassembly (?:language|code|programming)\b|\bx86\b|(?-i:\b(?:ARM|MIPS|RISC-V|AVR)) assembly\b|"
                 r"(?<=\bc, )assembly\b|(?<=\bc/)assembly\b|(?<=\bc\+\+, )assembly\b|(?<=\bc\+\+/)assembly\b", ["Hardware"]),
    "OCaml/Haskell": (r"\bocaml\b|\bhaskell\b|\bf#\b", ["Quant", "Software"]),
    "Dart/Flutter": (r"\bdart\b|\bflutter\b", ["Software"]),
    "Solidity": (r"\bsolidity\b|\bweb3\b|\bsmart contracts?\b", ["Software"]),
    "React": (r"\breact(?:\.js|js)\b|(?-i:\bReact\b)(?! native)", ["Software", "Product/Design"]),  # not "react quickly"
    "React Native": (r"\breact native\b", ["Software"]),
    "Next.js": (r"\bnext\.?js\b", ["Software"]),
    # not a "process node", "express interest" or American Express
    "Node.js": (r"\bnode\.?js\b|(?-i:\bNode\b)|\bexpress\.?js\b|\bnestjs\b", ["Software"]),
    "Vue/Angular/Svelte": (r"\bvue(?:\.js)?\b|\bangular\b|\bsvelte\b", ["Software"]),
    "Django/Flask/FastAPI": (r"\bdjango\b|\bflask\b|\bfastapi\b", ["Software"]),
    # the Java framework, not the Spring term
    "Spring": (r"\bspring (?:boot|framework|mvc|cloud|security|batch|webflux)\b|"
               r"(?:(?<=\bjava/)|(?<=\bjava, )|(?<=\bjava )|(?<=\bjava and )|(?<=\bjava & ))spring\b(?!\s*['’]?\d)|"
               r"\bspring(?=\s*(?:[/,&]|and)\s*(?:java|hibernate)\b)", ["Software"]),
    "PyTorch": (r"\bpytorch\b|\btorch\b", ["AI/ML/Data"]),
    "TensorFlow/Keras": (r"\btensorflow\b|\bkeras\b|\bjax\b", ["AI/ML/Data"]),
    "scikit-learn": (r"\bscikit[- ]learn\b|\bsklearn\b|\bxgboost\b|\blightgbm\b", ["AI/ML/Data"]),
    "Pandas/NumPy": (r"\bpandas\b|\bnumpy\b|\bscipy\b|\bpolars\b", ["AI/ML/Data", "Quant"]),
    "LLMs/GenAI": (r"\bllms?\b|\blarge language models?\b|\bgenai\b|\bgenerative ai\b|\brag\b|\blangchain\b|"
                   r"\bhugging ?face\b|\btransformers?(?:-based|[- ]models?|[- ]architectures?|[- ]networks?)\b|"
                   r"\bprompt engineering\b|\bopenai\b|\bclaude\b", ["AI/ML/Data"]),  # not power transformers
    "Computer Vision": (r"\bcomputer vision\b|\bopencv\b|\byolo\b|\bimage processing\b", ["AI/ML/Data"]),
    # Spark and Airflow written as tools, not "spark ideas" or airflow in a thermal design
    "Spark/Big Data": (r"(?-i:\bSpark\b)|\bapache spark\b|\bspark sql\b|\bpyspark\b|\bhadoop\b|\bkafka\b|"
                       r"(?-i:\bAirflow\b)|\bapache airflow\b|\bdbt\b|\bdatabricks\b|\bsnowflake\b|\bbigquery\b|"
                       r"\bredshift\b", ["AI/ML/Data"]),
    "Statistics": (r"\bstatistic\w*\b|\bprobability\b|\bstochastic\b|\btime series\b|\beconometric\w*\b|"
                   r"\bregression\b(?!\s+(?:test\w*|suites?|bugs?)\b)", ["AI/ML/Data", "Quant"]),  # not regression tests
    "AWS": (r"\baws\b|\bamazon web services\b|\bec2\b|\bs3\b", ["Software"]),
    "GCP": (r"\bgcp\b|\bgoogle cloud\b", ["Software"]),
    "Azure": (r"\bazure\b", ["Software"]),
    "Docker/Kubernetes": (r"\bdocker\b|\bkubernetes\b|\bk8s\b|\bcontaineri[sz](?:ed|ation|ing)\b|"
                          r"\bcontainer orchestration\b", ["Software"]),  # not shipping containers
    "Terraform/IaC": (r"\bterraform\b|\bansible\b|\binfrastructure as code\b", ["Software"]),
    "CI/CD": (r"\bci/cd\b|\bgithub actions\b|\bjenkins\b|\bcircleci\b|\bcontinuous integration\b", ["Software"]),
    "Git": (r"\bgit\b|\bgithub\b|\bgitlab\b", ["Software"]),
    "Linux": (r"\blinux\b|\bunix\b", ["Software", "Hardware"]),
    "Databases": (r"\bmongodb\b|\bredis\b|\bdynamodb\b|\bcassandra\b|\bfirebase\b|\bsupabase\b|\bnosql\b", ["Software"]),
    "APIs": (r"(?-i:\bREST\b)|\brestful\b|\brest apis?\b|\bgraphql\b|\bgrpc\b|\bapis?\b|\bmicroservices?\b",
             ["Software"]),  # not "the rest of the team"
    "Distributed Systems": (r"\bdistributed systems?\b|\bconcurrency\b|\bconcurrent (?:programming|systems?|computing)\b|"
                            r"\bmulti-?thread\w*\b|\blow[- ]latency\b", ["Software", "Quant"]),  # not "concurrently enrolled"
    "Security": (r"\bcyber ?security\b|\bpenetration testing\b|\bowasp\b|\bsiem\b|\bnetwork security\b", ["Software"]),
    # embedded systems, not "embedded in the product team"
    "Embedded": (r"\bembedded[ /-](?:systems?|software|firmware|linux|devices?|programming|development|engineer\w*|"
                 r"developer|hardware|controllers?|electronics|processors?|appliances?|products?|applications?|platforms?|"
                 r"c\b|c\+\+|rust|intern\w*|co-?op)|\bmicrocontrollers?\b|\barduino\b|\braspberry pi\b|\bstm32\b|\brtos\b|"
                 r"\bfreertos\b", ["Hardware"]),
    "FPGA/ASIC": (r"\bfpga\b|\basic\b|\bvivado\b|\bquartus\b|(?-i:\bCadence\b)|\bsynopsys\b", ["Hardware"]),  # not a weekly cadence
    "Circuits/PCB": (r"\bpcb\b|\bcircuit design\b|\baltium\b|\bkicad\b|\bspice\b|\banalog (?:and|&) mixed[- ]signal\b|"
                     r"\banalog(?:/|[ -])(?:mixed[- ]signal|circuits?|design|electronics|ics?|signals?|front[- ]ends?|layout|"
                     r"filters?)\b", ["Hardware"]),
    "CAD": (r"\bsolidworks\b|\bautocad\b|\bfusion 360\b|\bcatia\b|\bansys\b", ["Hardware"]),
    "ROS/Robotics": (r"\bros2?\b|\brobotics\b|\bslam\b|\bgazebo\b", ["Hardware", "AI/ML/Data"]),
    "Unity/Unreal": (r"\bunity\b|\bunreal\b|\bgame dev\w*\b|\bgodot\b", ["Software"]),
    "iOS/Android": (r"\bios\b|\bandroid\b|\bxcode\b|\bjetpack compose\b", ["Software"]),
    # design tools and practice: a "prototype" or a "sketch" can be anything
    "Figma": (r"\bfigma\b|(?-i:\bSketch\b)|\bwireframes?\b|\buser research\b", ["Product/Design"]),
    # product management phrases (stakeholders, agile and scrum are in every kind of posting)
    "Product Sense": (r"\bproduct roadmaps?\b|\buser stories\b|\ba/b test\w*\b|\bprds?\b|\bproduct strategy\b",
                      ["Product/Design"]),
    "Excel/VBA": (r"\bexcel\b(?!\s+(?:in|at|as|within|under|throughout)\b)(?<!\bwill excel)(?<!['’]ll excel)|\bvba\b|"
                  r"\bfinancial model\w*\b", ["Business/Finance", "Quant"]),  # not "you'll excel in"
    "BI Tools": (r"\btableau\b(?! de\b)|\bpower ?bi\b|\blooker\b|\bqlik\b", ["AI/ML/Data", "Business/Finance"]),
    # finance work, not "diversity, equity and inclusion", "salary and equity", "your portfolio" or "a $2B valuation"
    "Finance": (r"\bderivatives\b|\boptions pricing\b|\bportfolio (?:management|managers?|construction|"
                r"optimi[sz]ation|analytics|risk|theory)\b|\bfixed income\b|\bequity (?:research|trading|markets?|"
                r"derivatives|capital markets)\b|\bprivate equity\b|\bequities\b|"
                r"(?<!\d[bmk] )(?<!\d )(?<!illion )\bvaluation\b|\bdcf\b|\bbloomberg terminal\b|\bcfa\b",
                ["Business/Finance", "Quant"]),
    # what quant and systems roles keep asking for (each track's Market). "Develop algorithms for our robots" isn't
    # the CS fundamentals; "our AI-powered operating system" is a product; a trading firm describing itself isn't a
    # requirement
    "Algorithms": (r"\bdata structures?\b|\balgorithm (?:design|analysis)\b|\b(?:design and )?analysis of algorithms\b",
                   ["Software", "Quant"]),
    "Competitive Programming": (r"\bcompetitive programming\b|\bcodeforces\b|\bicpc\b|\btopcoder\b|\batcoder\b|"
                                r"\bprogramming contests?\b|\bputnam\b|\bmath(?:ematics|ematical)? olympiads?\b|"
                                r"\beuclid (?:math )?contest\b", ["Quant", "Software"]),
    "Operating Systems": (r"\boperating systems\b|\boperating system (?:concepts|internals|fundamentals|design|"
                          r"principles)\b|\blinux kernel\b|\bkernel (?:development|programming|drivers?|modules?)\b|"
                          r"\bos internals\b|\bsystems programming\b|\brtos\b", ["Software", "Hardware", "Quant"]),
    "Networking (TCP/IP)": (r"\btcp(?:/ip)?\b|\budp\b|\bnetwork(?:ing)? protocols?\b|\bcomputer networks?\b|"
                            r"\bsocket programming\b|\bmulticast\b", ["Software", "Quant"]),
    "KDB+/q": (r"\bkdb\+?|\bq/kdb\b", ["Quant", "AI/ML/Data"]),
    "Trading": (r"\border books?\b|\bmarket microstructure\b|\bhigh[- ]frequency trading\b|\bhft\b|"
                r"\balgo(?:rithmic)? trading\b|\bexecution algorithms?\b|\bmarket[- ]making (?:strategies|models?|"
                r"concepts)\b", ["Quant"]),
    "Mental Math": (r"\bmental (?:math|maths|arithmetic)\b|\bbrain ?teasers?\b|\bprobability puzzles?\b|"
                    r"\bquick (?:mental )?(?:math|arithmetic)\b|\bnumerical reasoning\b", ["Quant"]),
}
SKILLS = {name: (re.compile(rx, 0 if name in ("C", "R", "Go") else re.I), cats) for name, (rx, cats) in _SK.items()}
# skills whose patterns were narrowed (October 2026: "Spring 2027", "equity", "stakeholders"... no longer count). Tags
# stored before that are checked again by store.refresh_skills.
NARROWED_SKILLS = {"C#", "Swift", "Ruby", "Assembly", "React", "Node.js", "Spring", "LLMs/GenAI", "Spark/Big Data",
                   "Statistics", "AWS", "Docker/Kubernetes", "APIs", "Distributed Systems", "Embedded", "FPGA/ASIC",
                   "Circuits/PCB", "Figma", "Product Sense", "Excel/VBA", "BI Tools", "Finance"}


def detect_skills(text: str) -> list[str]:
    if not text:
        return []
    return [name for name, (rx, _) in SKILLS.items() if rx.search(text)]


def skill_categories(skill: str) -> list[str]:
    return SKILLS.get(skill, (None, []))[1]


# --- flags -------------------------------------------------------------------

VISA_YES = re.compile(
    r"\b(?:will|can|able to|happy to|we)\s+(?:provide|offer|sponsor|support)\s+(?:\w+\s+){0,2}?(?:visas?|sponsorship|"
    r"work authori[sz]ation)|visa sponsorship (?:is )?(?:available|provided|offered|possible)|sponsorship (?:is )?"
    r"available|\bJ-?1\b|\bTN (?:visa|status)\b|international (?:students|candidates|applicants) (?:are )?(?:welcome|"
    r"encouraged|eligible)|(?:students|candidates|applicants) (?:from|in|based in|located in) canada|canadian "
    r"(?:students|universities|co-?op)|(?:remote|work) (?:from|in) canada|\bwe\s+(?:do\s+|will\s+|can\s+)?sponsor\b|"
    r"take over (?:\w+\s+)?sponsorship|sponsorship of (?:employment |work )?visas", re.I)
NO_SPONSOR = re.compile(
    r"(?:unable|not able|cannot|can't|can not|will not|won't|do not|does not|don't|doesn't|are not able|is not able)"
    r"\s+(?:to\s+)?(?:provide|offer|support|sponsor)\s*(?:[\w-]+\s+){0,3}?(?:visas?|sponsorship|immigration)|"
    r"(?:do not|does not|will not|won't|cannot|unable to|not able to)\s+sponsor\b|without (?:the need for )?"
    r"(?:current or future |now or in the future |any )?(?:visa |employment |immigration )?sponsorship|"
    r"no (?:visa )?sponsorship|sponsorship (?:is )?not (?:available|provided|offered)|not (?:eligible for|offering) "
    r"(?:visa )?sponsorship", re.I)


_STUDENT_VISA = re.compile(r"\b(?:CPT|OPT|STEM OPT|F-1|F1 visa)\b")  # case-sensitive on purpose ("opt in")


# Who can't apply, only where a posting says so. Not "military" (it holds "itar", and every equal-opportunity statement has it), not
# "U.S. Citizenship and Immigration Services" (E-Verify's small print), not "a U.S. citizen or anyone authorized to work here".
_US = r"(?:u\.?s\.?a?\.?|united states)"
_US_ONLY_RE = re.compile(
    rf"\b{_US} citizen(?:ship)?\s*(?:status\s*)?(?:is\s+|are\s+|will be\s+|be\s+)?(?:strictly\s+|also\s+|absolutely\s+)?(?:required|mandatory|needed|only|a pre-?requisite)\b"
    rf"|\b(?:requires?|requiring|requirement of|need)\s+(?:active\s+|a\s+|an\s+|to\s+have\s+|to\s+be\s+(?:a\s+)?)?{_US} citizen(?:ship)?\b"
    rf"|\bmust\s+(?:either\s+)?(?:be|have|hold|possess)\s+(?:a\s+|an\s+)?(?:\(i\)\s*)?(?:current\s+|valid\s+)?[\"“]?{_US} (?:citizen(?:ship|s)?|persons?)\b"
    rf"|\bonly\s+{_US} (?:citizens?|persons?)\b|\bopen to {_US} citizens\b|\bonly to applicants who qualify as [\"“]?{_US} persons?\b"
    rf"|\bstatus of a {_US} person\b|\b{_US} citizen(?:ship|s)?,? or (?:{_US} )?(?:lawful )?(?:permanent|green)"
    r"|\bsecurity clearance\b|\b(?:top )?secret clearance\b|\bts/sci\b|(?-i:\bITAR\b)", re.I)
_WORK_ALTERNATIVE_RE = re.compile(
    r"\bor\b[^.;\n]{0,160}\b(?:otherwise\s+)?(?:legally?\s+)?(?:authori[sz]ed to work|right to work|"
    r"alien authori[sz]ed|alien lawfully)", re.I)


def us_only(text: str) -> bool:
    """Read exceptions in the restriction's clause, never elsewhere in the posting."""
    text = re.sub(r"\bu\.s\.(?:a\.)?", "US", text, flags=re.I)
    for clause in re.split(r"[;.!?\n]+", text):
        for m in _US_ONLY_RE.finditer(clause):
            before, after = clause[:m.start()], clause[m.end():]
            # A direct negation of this requirement, not a separate sentence about sponsorship.
            if re.search(r"\b(?:does not|do not|will not|not|no)\s*$", before, re.I):
                continue
            if re.match(r"\s+(?:(?:is|are)\s+)?not\s+(?:required|needed|necessary)\b", after, re.I):
                continue
            if "clearance" in m.group().lower() or "ts/sci" in m.group().lower():
                if (re.search(r"\b(?:Canadian|Canada|UK|United Kingdom|BPSS|NATO|reliability)\s*$", before, re.I)
                        or re.match(r"\s*(?:\(Reliability Status\)|(?:from|in|for) (?:Canada|the UK|the United Kingdom)\b)", after, re.I)):
                    continue
            elif _WORK_ALTERNATIVE_RE.search(after):
                continue
            return True
    return False


_DEGREE_OPEN_RE = re.compile(r"\b(bs|b\.s\.|ba|bsc|bachelor'?s?|undergrad\w*)\b", re.I)
# "Intern, MS - Summer 2027", "Intern - MS", "MS/PhD": the degree, not a state ("Hattiesburg, MS") or "MS Office"
_MS_RE = re.compile(r"\b(?:[Ii]ntern(?:ship)?|[Cc]o-?op|[Ee]ngineer|[Ss]cientist|[Rr]esearcher|[Aa]nalyst)\s*[,(-]\s*MS\b|\s-\s+MS\b|\bMS\s*(?:/|,|or)\s*Ph\.?D")
_PHD_RE = re.compile(r"\bph\.?d\b|\bdoctoral\b|\bpost-?doc\w*\b", re.I)
_MASTERS_RE = re.compile(r"\b(master'?s|msc|m\.s\.|mba)\b|\bgraduate (?:intern|student|research)\b", re.I)
_DEGREE_ANY_RE = re.compile(r"MS\b|(?i:ph\.?d|doctoral|post-?doc|master|msc|m\.s\.|mba|graduate )")  # most titles: one look
DEGREE_FLAGS = {"phd", "masters"}


def degree_flags(title: str) -> list[str]:
    """Which degree the title itself asks for. "BS/MS" is open to undergrads; "MS/PhD" is open to a master's student."""
    if not _DEGREE_ANY_RE.search(title) or _DEGREE_OPEN_RE.search(title):
        return []
    if _MS_RE.search(title) or _MASTERS_RE.search(title):
        return ["masters"]
    return ["phd"] if _PHD_RE.search(title) else []


def detect_flags(title: str, description: str = "", sponsorship: str = "") -> list[str]:
    flags = []
    t = f"{title} {description[:6000]}"
    yes = bool(VISA_YES.search(t) or _STUDENT_VISA.search(t)) or "Offers Sponsorship" in (sponsorship or "")
    # "...without sponsorship for an export license" is export-control boilerplate, not a visa refusal
    no = any("export" not in t[m.end():m.end() + 40].lower() for m in NO_SPONSOR.finditer(t)) or \
        "Does Not Offer" in (sponsorship or "")
    if yes and no and not (_STUDENT_VISA.search(t) or re.search(r"\bJ-?1\b|\bTN\b|canad|international students", t, re.I)):
        yes = False  # a refusal beats generic sponsorship wording, unless a student route is spelled out
    if yes:
        flags.append("sponsor_yes")
    if no:
        flags.append("no_sponsor")
    flags += degree_flags(title)
    if us_only(t) or "Citizenship" in (sponsorship or ""):
        flags.append("us_citizen")
    if re.search(r"\bnew grad|graduate program|full[- ]time\b", title, re.I):
        flags.append("not_intern")
    if re.search(r"\bco-?op\b|\bwork term\b", t, re.I):
        flags.append("coop")
    if re.search(r"\b(freshman|sophomore|first[- ]year|second[- ]year|explore|step|early career program)\b",
                 title, re.I):
        flags.append("early_program")
    return flags


# --- misc --------------------------------------------------------------------

_DROP_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "ref", "source", "src",
                "gh_src", "lever-source", "trk", "refid", "trackingid", "position", "pagenum", "from"}


def clean_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
        q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in _DROP_PARAMS]
        return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), urlencode(q), ""))
    except ValueError:
        return url


_SUFFIX_RE = re.compile(
    r"\b(inc|incorporated|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|plc|gmbh|sa|ag|lp|llp|"
    r"technologies|technology|holdings|group)\b\.?", re.I)


def company_key(name: str) -> str:
    s = (name or "").lower().replace("&", " and ")
    s = re.sub(r"\(.*?\)", " ", s)
    s = _SUFFIX_RE.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def title_key(title: str) -> str:
    s = (title or "").lower()
    s = re.sub(r"[^a-z0-9+#]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_relative_posted(text: str, now: float | None = None) -> int | None:
    """Workday-style 'Posted 3 Days Ago' / 'Posted Today' / 'Posted 30+ Days Ago'."""
    now = now or time.time()
    if not text:
        return None
    t = text.lower()
    if "today" in t:
        return int(now)
    if "yesterday" in t:
        return int(now - 86400)
    m = re.search(r"(\d+)\+?\s*day", t)
    if m:
        return int(now - int(m.group(1)) * 86400)
    m = re.search(r"(\d+)\+?\s*(week|month)", t)
    if m:
        mult = 7 if m.group(2) == "week" else 30
        return int(now - int(m.group(1)) * mult * 86400)
    return None


def parse_iso(ts) -> int | None:
    if ts is None or ts == "":
        return None
    if isinstance(ts, (int, float)):
        return int(ts / 1000) if ts > 1e11 else int(ts)
    try:
        s = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return None


def strip_html(html: str) -> str:
    if not html:
        return ""
    import html as _html
    s = _html.unescape(html)
    s = re.sub(r"<(br|/p|/li|/h\d|/div)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<li[^>]*>", "• ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


# --- requisition IDs -------------------------------------------------------------------------------
# The company's own number for a posting (Workday "R-0000181145", Greenhouse "CSQ427R266", iCIMS "2025-12345").
# Confirmation, interview and rejection emails often quote it, which ties an email to the exact posting even when a
# company has several near-identical intern roles open.

_REQ_TOKEN = r"([A-Za-z]{0,6}[-_]?\d[\w-]{2,24})"
_REQ_LABELED = re.compile(
    r"(?:\b(?:job\s*)?req(?:uisition)?\.?(?:\s*(?:id|number|no\.?|#))?|\bjob\s*(?:id|number|no\.?|#|code)|"
    r"\b(?:posting|reference|ref|opening|vacancy)\s*(?:id|number|no\.?|#))\s*[:#]?\s*" + _REQ_TOKEN, re.I)
_REQ_BARE = re.compile(r"(?<![\w-])((?:JR|REQ|R)-?\d{5,10})(?:-\d{1,2})?(?![\w-])")  # Workday-style, unlabeled
_REQ_URL = [
    re.compile(r"myworkdayjobs\.com/.*_((?:[A-Z]{1,5}-?)?\d{4,}[A-Za-z0-9]*)(?:-\d{1,2})?(?:[/?#]|$)"),
    re.compile(r"icims\.com/jobs/(\d{3,})", re.I),
    re.compile(r"/job/[^/?#]+/(\d{6,})/?(?:[?#]|$)"),  # SAP SuccessFactors
    re.compile(r"oraclecloud\.com/.*/job/(\d{3,})", re.I),
    re.compile(r"[?&]job=([\w-]{3,30})", re.I),  # Taleo
    re.compile(r"careers\.kpmg\.ca/jobs/(\d{3,})", re.I),  # Jibe
]


def _valid_req(tok: str) -> bool:
    digits = sum(ch.isdigit() for ch in tok)
    return 3 <= len(tok) <= 30 and digits >= 3 and not re.fullmatch(r"(?:19|20)\d\d", tok) and " " not in tok


def find_req_ids(text: str, labeled_only: bool = False) -> list[str]:
    """Requisition numbers quoted in text ("Req ID: R-0000181145", "Job #2025-1234", "... Intern (JR123456)")."""
    out: list[str] = []
    for m in _REQ_LABELED.finditer(text or ""):
        tok = m.group(1).strip("-_")
        if _valid_req(tok) and tok not in out:
            out.append(tok)
    if not labeled_only:
        for m in _REQ_BARE.finditer(text or ""):
            if m.group(1) not in out and not any(same_req(m.group(1), o) for o in out):
                out.append(m.group(1))
    return out


def extract_req_id(url: str, text: str = "", hint: str = "") -> str | None:
    """Best requisition number for a posting: the board's own field, then a labeled one in the description, then
    the one baked into the job link."""
    hint = (hint or "").strip()
    if hint and _valid_req(hint):
        return hint
    if text:
        found = find_req_ids(text[:4000] + "\n" + text[-1500:], labeled_only=True)
        if found:
            return found[0]
    for rx in _REQ_URL:
        m = rx.search(url or "")
        if m and _valid_req(m.group(1)):
            return m.group(1)
    return None


def req_key(x: str) -> str:
    """'R-0000181145' and 'r181145' compare equal."""
    s = re.sub(r"[^a-z0-9]", "", (x or "").lower())
    return re.sub(r"(?<![0-9])0+(?=[0-9])", "", s)


def same_req(a: str, b: str) -> bool:
    ka, kb = req_key(a), req_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    da, db_ = re.sub(r"[a-z]", "", ka), re.sub(r"[a-z]", "", kb)  # "Job ID 181145" vs "R-0000181145"
    return len(da) >= 5 and da == db_ and (ka.isdigit() or kb.isdigit())


def when_text(ts: float, with_time: bool = True) -> str:
    """"Mon Oct 14, 2:30 PM" on every computer (strftime's %-I / %-d only exist on Mac and Linux)."""
    d = datetime.fromtimestamp(ts)
    day = d.strftime("%a %b %d")
    if not with_time:
        return day
    return f"{day}, {d.hour % 12 or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"


def long_date(d) -> str:
    """"October 14, 2026" without a leading zero, on every computer."""
    return f"{d.strftime('%B')} {d.day}, {d.year}"
