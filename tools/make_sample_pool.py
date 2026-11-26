"""Generate a pool of synthetic resumes with an answer key, for testing search at scale.

    python tools/make_sample_pool.py            # 500 resumes into sample_pool_500/
    python tools/make_sample_pool.py --n 1000 --out sample_pool_1000

Needs reportlab for PDFs (pip install reportlab); without it every resume is written as DOCX.
Every resume is fictional. The answer key (answer_key.csv) marks which candidates genuinely
fit the two sample jobs, so search recall can be measured instead of guessed.
"""
from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

import docx

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    HAVE_PDF = True
except ImportError:
    HAVE_PDF = False

YEAR = 2026
FOOTER = "Synthetic resume created for demo purposes. Not a real person."

FIRST = """Aarav Aditi Akash Ananya Anjali Arjun Aryan Bhavna Chitra Deepak Devika Dhruv Divya Farhan Gaurav Gayatri
Harish Ishaan Jasleen Jaya Karan Kavya Kiran Lakshmi Madhav Manish Meera Mohit Nandini Naveen Neeraj Nikhil Nisha
Pallavi Pooja Pranav Priya Rahul Rajesh Ravi Reshma Ritika Rohit Sahil Sameer Sana Sandeep Shreya Siddharth Simran
Sneha Suresh Swati Tanvi Tarun Uday Varun Vidya Vikas Yamini Zoya Abdul Fathima Joseph Mary Thomas Anil Bindu""".split()
LAST = """Agarwal Ahmed Bhat Chatterjee Das Desai Dutta Fernandes George Ghosh Gupta Hegde Iyer Jain Joshi Kapoor Khan
Kumar Kurian Mathew Mehta Menon Mishra Mukherjee Nair Naidu Pandey Patel Pillai Prasad Rao Reddy Roy Saxena Sen
Shah Sharma Shetty Singh Sinha Srinivasan Thakur Tiwari Varghese Verma Yadav Zaidi Bose Chopra Malhotra Krishnan""".split()
CITIES = ["Bengaluru", "Mumbai", "Pune", "Delhi", "Gurugram", "Noida", "Hyderabad", "Chennai", "Kochi", "Kolkata",
          "Ahmedabad", "Jaipur", "Indore", "Coimbatore", "Thiruvananthapuram", "Chandigarh", "Lucknow"]
STEMS = ["Northwind", "Bluepeak", "Cedar", "Kestrel", "Harbour", "Silverline", "Tern", "Quill", "Lumen", "Meridian",
         "Saffron", "Indigo", "Banyan", "Monsoon", "Granite", "Orbit", "Peacock", "Riverstone", "Summit", "Vega",
         "Coral", "Aurora", "Falcon", "Lotus", "Maple", "Neem", "Pinnacle", "Sterling", "Zenith", "Arcadia"]
SECTOR_SUFFIX = {
    "bfsi": ["Finance", "Capital", "Bank", "Insurance", "Credit", "Fincorp", "Lending", "Securities"],
    "tech": ["Technologies", "Labs", "Systems", "Software", "Cloud", "Digital"],
    "retail": ["Retail", "Foods", "Mart", "Consumer", "Brands"],
    "ops": ["Logistics", "Manufacturing", "Industries", "Freight", "Motors"],
    "media": ["Media", "Studios", "Publishing", "News"],
    "health": ["Health", "Hospitals", "Pharma", "Diagnostics"],
    "edu": ["Learning", "Academy", "Schools", "EdTech"],
    "services": ["Consulting", "Services", "Advisory", "Partners"],
}
UNIS = ["University of Mumbai", "Delhi University", "University of Madras", "Osmania University", "Anna University",
        "Christ University", "Symbiosis International University", "Amity University", "VIT Vellore", "Manipal Academy",
        "Jadavpur University", "University of Kerala", "Pune University", "Bangalore University", "NIT Calicut",
        "Loyola College Chennai", "St. Xavier's College Mumbai", "XLRI Jamshedpur", "TISS Mumbai", "IIM Indore",
        "NMIMS Mumbai", "IIT Madras", "BITS Pilani", "Calcutta University", "Gujarat University"]


def company(rng, sector):
    return f"{rng.choice(STEMS)} {rng.choice(SECTOR_SUFFIX[sector])}"


# ---------- Role families ----------
# Each family returns (title, sector, summary_line, bullets, skills, degree) for the current and past roles.

def fam_hrbp_strong(rng, synonyms):
    sector = rng.choice(["ops", "retail", "tech", "health", "bfsi"])
    size = rng.choice([300, 450, 600, 800, 1200, 2000])
    hris = rng.choice(["SuccessFactors", "Darwinbox", "Oracle HCM", "Keka"]) if synonyms else "Workday"
    drop = rng.choice([(26, 18), (31, 22), (24, 15), (35, 24), (22, 14)])
    if synonyms == "unseen":  # wording that is deliberately NOT in the search synonym list
        hris = rng.choice(["Zoho People", "greytHR", "BambooHR"])
        title = rng.choice(["Business HR Manager", "HR Manager - Plant", "Site HR Lead"])
        summary = f"Business-facing HR manager for a {size}-person {rng.choice(['factory', 'warehouse network', 'contact centre'])}; first point of contact for unit heads."
        bullets = [
            f"Resolves worker issues and conduct cases; runs goal-setting and year-end reviews for {size} staff.",
            f"Publishes monthly headcount and exit-trend reports from Google Sheets; brought quarterly resignations down by a third with a buddy programme.",
        ]
        skills = ["Conduct Cases", "Year-end Reviews", hris, "Google Sheets", "Buddy Programme", "Labour Law"]
    elif synonyms:
        title = rng.choice(["People Partner", "HR Generalist", "Senior HR Generalist", "People Partner (Operations)"])
        summary = f"People partner for a {size}-person {rng.choice(['plant', 'business unit', 'sales organisation', 'delivery centre'])}, working closely with business heads."
        bullets = [
            f"Handles grievances and disciplinary inquiries end to end; runs the annual appraisal cycle (PMS) for {size} staff.",
            f"Built monthly people-metrics dashboards in MS-Excel; reduced early exits from {drop[0]}% to {drop[1]}% with a new induction plan.",
        ]
        skills = ["Grievance Handling", "Appraisals (PMS)", hris, "People Metrics", "MS-Excel", "Induction", "Labour Law"]
    else:
        title = rng.choice(["HR Business Partner", "Senior HR Business Partner", "HRBP"])
        summary = f"HR business partner for a {size}-person {rng.choice(['operations', 'sales', 'technology', 'retail'])} business."
        bullets = [
            f"Partners business heads on workforce planning; handles employee relations cases and runs performance management for {size} employees.",
            f"Uses HR analytics in Excel and {hris} to track attrition; cut first-year attrition from {drop[0]}% to {drop[1]}% with structured onboarding.",
        ]
        skills = ["Employee Relations", "Performance Management", hris, "HR Analytics", "Excel", "Onboarding", "Talent Reviews"]
    past_title = rng.choice(["HR Executive", "HR Generalist", "Assistant Manager - HR", "HR Officer"])
    past = (past_title, rng.choice(["retail", "ops", "services"]),
            rng.choice(["Ran performance cycles and handled employee relations cases for store staff.",
                        "Managed joining formalities, exit interviews and grievance redressal for 250 employees.",
                        "Supported appraisal cycles and maintained HR MIS in Excel."]))
    degree = rng.choice(["MA Human Resource Management", "MBA (HR)", "PGDM - Human Resources", "MA HRM & LR", "MSW (HRM)"])
    return title, sector, summary, bullets, skills, degree, past, (4, 10)


def fam_hr_near(rng, kind):
    sector = rng.choice(["tech", "retail", "ops", "services", "bfsi"])
    if kind == "ta":
        title = rng.choice(["Talent Acquisition Specialist", "Recruiter", "Senior Recruiter", "TA Lead"])
        summary = "Recruiter hiring for technology and sales roles."
        bullets = [f"Closed {rng.randint(40, 160)} hires a year across tech and sales; sourcing through LinkedIn Recruiter and Naukri.",
                   "Tracks hiring funnel metrics in Excel; manages offers in Workday."]
        skills = ["Sourcing", "LinkedIn Recruiter", "Naukri", "Interviewing", "Excel", "Workday", "Offer Negotiation"]
        exp = (3, 8)
    elif kind == "payroll":
        title = rng.choice(["Payroll Specialist", "Compensation Analyst", "Payroll & Benefits Executive"])
        summary = "Payroll and compensation professional managing monthly payroll and statutory compliance."
        bullets = [f"Runs monthly payroll for {rng.randint(400, 3000)} employees; files PF, ESI and TDS returns.",
                   "Builds salary benchmarking sheets in Excel for the annual increment cycle."]
        skills = ["Payroll", "PF/ESI", "TDS", "Excel", "Compensation Benchmarking", "SAP HR"]
        exp = (3, 9)
    elif kind == "ld":
        title = rng.choice(["Learning & Development Specialist", "L&D Manager", "Training Manager"])
        summary = "L&D professional designing leadership and sales training programmes."
        bullets = [f"Designed {rng.randint(10, 40)} training programmes a year; trained {rng.randint(300, 2000)} employees.",
                   "Measures training effectiveness with pre and post assessments."]
        skills = ["Training Needs Analysis", "Instructional Design", "LMS", "Facilitation", "PowerPoint"]
        exp = (4, 10)
    elif kind == "junior":
        title = rng.choice(["HR Executive", "HR Trainee", "Junior HR Generalist"])
        summary = "Early-career HR executive supporting employee relations and performance reviews."
        bullets = ["Supports the HRBP on employee relations cases and performance reviews.",
                   "Maintains HR dashboards in Excel and updates employee records in Workday."]
        skills = ["Employee Relations", "Performance Reviews", "Excel", "Workday", "Onboarding"]
        exp = (1, 2.5)
    else:  # hris admin
        title = rng.choice(["HRIS Analyst", "HR Operations Specialist", "HR Systems Administrator"])
        summary = "HR operations specialist running HR systems and reporting."
        bullets = ["Administers Workday modules and builds HR reports for leadership.",
                   "Automated the monthly headcount report in Excel and Power BI."]
        skills = ["Workday", "HRIS", "HR Reporting", "Excel", "Power BI", "Data Migration"]
        exp = (3, 8)
    past = ("HR Associate", rng.choice(["services", "retail"]), "Handled joining formalities and HR documentation.")
    degree = rng.choice(["MBA (HR)", "BBA", "MA Psychology", "PGDM - Human Resources", "B.Com"])
    return title, sector, summary, bullets, skills, degree, past, exp


def fam_da_strong(rng, synonyms):
    if synonyms == "unseen":  # wording that is deliberately NOT in the search synonym list
        org = rng.choice(["housing finance company", "asset management firm", "stock broking firm"])
        viz = rng.choice(["Power BI", "Tableau"])
        title = rng.choice(["Risk Analytics Associate", "Portfolio Analytics Associate", "Quantitative Associate"])
        summary = f"Works at a {org}, turning customer and portfolio data into decisions for business teams."
        bullets = [f"Writes queries on the Oracle data warehouse and analyses portfolio trends in Jupyter notebooks.",
                   f"Built {viz} reports on delinquency and prepayment used by {rng.randint(15, 120)} managers."]
        skills = ["Oracle", "Jupyter", viz, "Excel", "Logistic Regression", "Delinquency Analysis"]
        sector = "bfsi"
    elif synonyms:
        org = rng.choice(["NBFC", "retail bank", "general insurer", "microfinance lender", "credit card issuer"])
        sql = rng.choice(["PostgreSQL", "MySQL", "SQL Server"])
        viz = rng.choice(["Power BI", "Tableau"])
        title = rng.choice(["Business Analyst", "Analytics Associate", "Risk Analyst", "MIS Analyst"])
        summary = f"Analyst at a {org}, turning loan and customer data into decisions for business teams."
        bullets = [f"Writes {sql} queries to clean disbursement and repayment data; automated weekly portfolio reports with pandas.",
                   f"Built {viz} dashboards for collections and early-default tracking used by {rng.randint(15, 120)} managers."]
        skills = [sql, "pandas", "NumPy", viz, "Excel", "Regression", "Credit Risk"]
        sector = "bfsi"
    else:
        viz = rng.choice(["Tableau", "Power BI"])
        title = rng.choice(["Data Analyst", "Senior Data Analyst", "Credit Risk Analyst"])
        summary = "Data analyst building credit-risk and customer dashboards in banking and financial services."
        bullets = [f"Writes SQL and Python to pull and clean loan data; built {viz} dashboards used by {rng.randint(20, 150)} branch managers.",
                   "Supports credit-risk analysis with clear findings for business teams; strong grounding in statistics."]
        skills = ["SQL", "Python", viz, "Excel", "Statistics", "Credit Risk", "BFSI"]
        sector = "bfsi"
    past = (rng.choice(["Junior Analyst", "MIS Executive", "Analyst"]), "bfsi",
            rng.choice(["Wrote SQL reports for claims analysis.", "Prepared monthly MIS for the lending team.",
                        "Maintained loan-book reports and reconciliations."]))
    degree = rng.choice(["B.Tech Computer Science", "M.Sc Statistics", "B.Sc Mathematics", "MBA Finance", "B.E. Electronics"])
    return title, sector, summary, bullets, skills, degree, past, (3.5, 8)


def fam_data_near(rng, kind):
    if kind == "ecom":
        title, sector = rng.choice(["Data Analyst", "Product Analyst"]), rng.choice(["retail", "tech"])
        summary = "Data analyst for an e-commerce marketplace."
        bullets = ["Writes SQL and Python for funnel and cohort analysis.", "Built Tableau dashboards for category managers."]
        skills, exp = ["SQL", "Python", "Tableau", "A/B Testing", "Excel"], (3, 7)
    elif kind == "bi":
        title, sector = rng.choice(["BI Developer", "Reporting Analyst"]), rng.choice(["bfsi", "services"])
        summary = "BI developer building management reports."
        bullets = ["Builds Power BI reports and SQL views for finance leadership.", "Maintains the reporting data mart."]
        skills, exp = ["Power BI", "SQL", "DAX", "SSIS", "Excel"], (3, 8)
    elif kind == "ds":
        title, sector = rng.choice(["Data Scientist", "ML Engineer"]), "tech"
        summary = "Data scientist building recommendation and forecasting models."
        bullets = ["Trains forecasting models in Python and deploys them on AWS.", "Ran experiments that lifted conversion by 6%."]
        skills, exp = ["Python", "scikit-learn", "TensorFlow", "AWS", "Statistics"], (3, 7)
    else:  # junior bfsi analyst
        title, sector = "Junior Data Analyst", "bfsi"
        summary = "Graduate analyst in a bank's analytics team."
        bullets = ["Writes SQL queries for loan reports and builds Excel trackers.", "Learning Python and Power BI."]
        skills, exp = ["SQL", "Excel", "Power BI", "Python (basic)"], (0.5, 2)
    past = ("Analyst Intern", sector, "Prepared reports and cleaned data for the analytics team.")
    degree = rng.choice(["B.Tech", "B.Sc Statistics", "BCA", "M.Sc Data Science"])
    return title, sector, summary, bullets, skills, degree, past, exp


GENERIC = {
    "swe": ("tech", ["Software Engineer", "Senior Software Engineer", "Backend Engineer", "Frontend Engineer", "Full Stack Developer"],
            "Engineer building web services and APIs.",
            ["Built REST APIs in {lang} serving {n} requests a day.", "Moved services to {cloud}; cut deployment time by half."],
            [["Java", "Go", "Node.js", "Python", "Kotlin"], ["AWS", "GCP", "Azure"], ["Docker", "Kubernetes", "PostgreSQL", "React", "Redis", "Git"]],
            ["B.Tech Computer Science", "B.E. Information Technology", "MCA"], (1, 12)),
    "sales": ("services", ["Sales Manager", "Key Account Manager", "Business Development Manager", "Area Sales Manager"],
              "Sales professional managing enterprise and channel accounts.",
              ["Grew territory revenue by {pct}% in two years.", "Manages {n} key accounts and a team of {t} executives."],
              [["Salesforce CRM", "HubSpot"], ["Negotiation", "Key Account Management", "Channel Sales", "Forecasting", "Excel"]],
              ["MBA Marketing", "BBA", "B.Com"], (2, 14)),
    "mkt": ("retail", ["Marketing Manager", "Brand Manager", "Digital Marketing Specialist", "Performance Marketer"],
            "Marketer running brand and performance campaigns.",
            ["Ran campaigns that lifted leads by {pct}%.", "Manages a monthly ad budget of Rs {n} lakh across Google and Meta."],
            [["Google Ads", "Meta Ads"], ["SEO", "Brand Strategy", "Google Analytics", "Content Marketing", "Excel"]],
            ["MBA Marketing", "BMS", "BA Mass Media"], (2, 12)),
    "content": ("media", ["Content Writer", "Senior Editor", "Correspondent", "Copywriter", "Content Strategist"],
                "Writer and editor for digital publications.",
                ["Writes and edits {n} stories a month.", "Grew newsletter subscribers by {pct}%."],
                [["WordPress", "CMS"], ["Editing", "SEO", "Reporting", "Copywriting", "Interviewing"]],
                ["BA Journalism", "MA English", "BA Mass Communication"], (1, 12)),
    "pm": ("tech", ["Product Manager", "Senior Product Manager", "Associate Product Manager"],
           "Product manager for consumer apps.",
           ["Launched {n} features; lifted retention by {pct}%.", "Runs discovery with design and engineering."],
           [["Jira", "Figma"], ["Roadmapping", "A/B Testing", "SQL", "Mixpanel", "User Research"]],
           ["MBA", "B.Tech"], (2, 12)),
    "design": ("tech", ["UX Designer", "Product Designer", "Visual Designer", "UI Designer"],
               "Designer for web and mobile products.",
               ["Redesigned onboarding; completion up {pct}%.", "Maintains the design system used by {n} designers."],
               [["Figma", "Sketch"], ["Prototyping", "User Research", "Design Systems", "Illustrator"]],
               ["B.Des", "M.Des", "BFA"], (1, 10)),
    "fin": ("bfsi", ["Relationship Manager", "Credit Officer", "Branch Operations Manager", "Accounts Manager", "Underwriter", "Chartered Accountant"],
            "Banking and finance professional.",
            ["Manages a loan book of Rs {n} crore.", "Handles credit appraisal and KYC for retail customers."],
            [["Excel", "Tally"], ["Credit Appraisal", "KYC", "Financial Analysis", "Banking Operations", "Risk Assessment"]],
            ["MBA Finance", "B.Com", "CA"], (2, 14)),
    "ops": ("ops", ["Operations Manager", "Supply Chain Analyst", "Logistics Manager", "Plant Manager", "Procurement Manager"],
            "Operations professional running supply chain and plant operations.",
            ["Cut logistics cost by {pct}% through route planning.", "Leads a team of {t} supervisors."],
            [["SAP MM", "Excel"], ["Inventory Planning", "Vendor Management", "Lean Six Sigma", "Procurement"]],
            ["B.E. Mechanical", "MBA Operations", "B.Tech"], (2, 15)),
    "support": ("services", ["Customer Support Lead", "Customer Success Manager", "Service Desk Analyst"],
                "Customer support professional.",
                ["Handles {n} tickets a month with 92% CSAT.", "Trained {t} new support executives."],
                [["Zendesk", "Freshdesk"], ["Customer Service", "Escalation Handling", "Excel", "CRM"]],
                ["B.Com", "BA", "BBA"], (1, 10)),
    "consult": ("services", ["Management Consultant", "Strategy Associate", "Business Consultant"],
                "Consultant working on growth and operations projects.",
                ["Delivered {n} client engagements in retail and BFSI.", "Built market-sizing models in Excel."],
                [["Excel", "PowerPoint"], ["Market Research", "Financial Modelling", "Stakeholder Management"]],
                ["MBA", "B.Tech"], (2, 10)),
    "other": ("edu", ["School Teacher", "Office Administrator", "Legal Associate", "Hospital Administrator", "Event Manager"],
              "Professional with experience in administration and coordination.",
              ["Coordinates schedules and vendors for a team of {t}.", "Managed {n} events and programmes a year."],
              [["MS Office"], ["Coordination", "Communication", "Scheduling", "Vendor Management"]],
              ["B.Ed", "BA", "LLB", "MBA"], (1, 15)),
}


def fam_generic(rng, key):
    sector, titles, summary, bullets, skill_banks, degrees, exp = GENERIC[key]
    fill = {"lang": rng.choice(["Java", "Go", "Node.js", "Python"]), "cloud": rng.choice(["AWS", "GCP", "Azure"]),
            "n": rng.randint(5, 900), "pct": rng.randint(8, 60), "t": rng.randint(3, 25)}
    skills = []
    for bank in skill_banks:
        skills += rng.sample(bank, k=min(len(bank), rng.randint(1, 3) if len(bank) <= 3 else rng.randint(3, 5)))
    past = (rng.choice(titles).replace("Senior ", ""), sector,
            rng.choice(["Started in the same function; owned day-to-day delivery for a regional team.",
                        "Joined as a graduate hire and was promoted within two years.",
                        "Handled client and internal reporting for the department."]))
    return (rng.choice(titles), sector, summary, [b.format(**fill) for b in bullets], skills,
            rng.choice(degrees), past, exp)


PLAN = [  # (family, variant, count, hrbp_label, da_label)
    ("hrbp", False, 4, "strong", "none"), ("hrbp", True, 4, "strong", "none"), ("hrbp", "unseen", 4, "strong", "none"),
    ("hrnear", "ta", 8, "near", "none"), ("hrnear", "payroll", 5, "near", "none"), ("hrnear", "ld", 5, "near", "none"),
    ("hrnear", "junior", 5, "near", "none"), ("hrnear", "hris", 5, "near", "none"),
    ("da", False, 4, "none", "strong"), ("da", True, 4, "none", "strong"), ("da", "unseen", 4, "none", "strong"),
    ("danear", "ecom", 10, "none", "near"), ("danear", "bi", 6, "none", "near"), ("danear", "ds", 6, "none", "near"),
    ("danear", "junior", 6, "none", "near"),
    ("swe", None, 70, "none", "none"), ("sales", None, 50, "none", "none"), ("mkt", None, 45, "none", "none"),
    ("content", None, 40, "none", "none"), ("pm", None, 30, "none", "none"), ("design", None, 25, "none", "none"),
    ("fin", None, 45, "none", "none"), ("ops", None, 40, "none", "none"), ("support", None, 30, "none", "none"),
    ("consult", None, 20, "none", "none"), ("other", None, 25, "none", "none"),
]


def build_profile(rng, fam, variant):
    if fam == "hrbp":
        return fam_hrbp_strong(rng, variant)
    if fam == "hrnear":
        return fam_hr_near(rng, variant)
    if fam == "da":
        return fam_da_strong(rng, variant)
    if fam == "danear":
        return fam_data_near(rng, variant)
    return fam_generic(rng, fam)


def resume_lines(rng, name, prof):
    title, sector, summary, bullets, skills, degree, past, (lo, hi) = prof
    years = round(rng.uniform(lo, hi) * 2) / 2
    years_txt = f"{years:g}"
    city = rng.choice(CITIES)
    email = name.lower().replace(" ", ".") + "@example.com"
    cur_co = company(rng, sector)
    start = YEAR - max(1, int(years * rng.uniform(0.45, 0.7)))
    first_job = YEAR - int(round(years))
    lines = [("name", name), ("contact", f"{title}, {cur_co} | {city} | {email}"),
             ("h", "Summary"), ("p", f"{summary} {years_txt} years of experience."),
             ("h", "Experience"), ("role", f"{title}, {cur_co} ({start} - present)")]
    lines += [("p", b) for b in bullets]
    if start > first_job:
        lines += [("role", f"{past[0]}, {company(rng, past[1])} ({first_job} - {start})"), ("p", past[2])]
    lines += [("h", "Skills"), ("p", ", ".join(skills)),
              ("h", "Education"), ("p", f"{degree}, {rng.choice(UNIS)}"),
              ("foot", FOOTER)]
    return lines, years


def write_pdf(path, lines):
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=A4, title="Resume")
    story = []
    for kind, text in lines:
        style = {"name": styles["Title"], "h": styles["Heading3"], "foot": styles["Italic"]}.get(kind, styles["BodyText"])
        story.append(Paragraph(text.replace("&", "&amp;"), style))
        if kind in ("contact", "foot"):
            story.append(Spacer(1, 6))
    doc.build(story)


def write_docx(path, lines):
    d = docx.Document()
    for kind, text in lines:
        if kind == "name":
            d.add_heading(text, level=1)
        elif kind == "h":
            d.add_heading(text, level=3)
        else:
            d.add_paragraph(text)
    d.save(str(path))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--out", default="sample_pool_500")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    plan = [(f, v, h, d) for f, v, c, h, d in PLAN for _ in range(c)]
    scale = args.n / len(plan)
    if scale != 1:  # keep the planted candidates, scale the rest
        planted = [p for p in plan if p[2] != "none" or p[3] != "none"]
        rest = [p for p in plan if p[2] == "none" and p[3] == "none"]
        plan = planted + [rest[i % len(rest)] for i in range(max(0, args.n - len(planted)))]
    rng.shuffle(plan)

    names, used = [], set()
    while len(names) < len(plan):
        n = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if n not in used:
            used.add(n)
            names.append(n)

    rows = []
    for i, ((fam, variant, hrbp, da), name) in enumerate(zip(plan, names), start=1):
        lines, years = resume_lines(rng, name, build_profile(rng, fam, variant))
        use_pdf = HAVE_PDF and rng.random() < 0.9
        fname = f"{i:03d}_{name.lower().replace(' ', '_')}.{'pdf' if use_pdf else 'docx'}"
        (write_pdf if use_pdf else write_docx)(out / fname, lines)
        phrasing = {True: "synonyms", False: "literal", "unseen": "unseen"}.get(variant, "") if fam in ("hrbp", "da") else ""
        rows.append({"file": fname, "name": name, "family": fam if fam in ("hrbp", "da") or variant is None else f"{fam}:{variant}",
                     "phrasing": phrasing, "years": years, "hrbp_job": hrbp, "data_analyst_job": da})
    with open(out / "answer_key.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} resumes and answer_key.csv to {out}/")


if __name__ == "__main__":
    main()
