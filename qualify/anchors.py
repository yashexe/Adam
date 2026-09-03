"""
Calibration anchors for the relevance judge.

Three frozen postings with known reference bands ride along in every judge
batch, unlabelled -- the judge scores them blind, exactly like live
postings. `semantic.save_scores` checks each anchor's score against its
band, warns on a miss, and never caches them. The point is drift
detection: judge scores are produced by an LLM whose prompt, model, or
batch composition can change, and a re-judged stability sample
(docs/qualify.md, "Judge stability, measured") showed a small
batch-composition shift that per-batch anchors make visible instead of
silent.

The bands are deliberately wide (+/-7 single-score jitter was measured on
the stability sample): an anchor outside its band means something moved,
not that one score wobbled.

Anchor texts are frozen here rather than read from harvest/ or the board
cache so they can never drift or expire out from under the calibration.

- high: company-m's Forward Deployed Engineer posting -- interview
  ground truth (he interviewed via Paraform; the judge's historical score
  was 92).
- mid: company-f's Senior DevOps/SRE posting -- adjacent engineering,
  ops rather than his data/integration depth (historical score 58).
- low: company-m's Senior Field Marketing Manager posting -- clearly
  not an engineering role (a marketing role at a company whose FDE posting
  is the high anchor, so company prestige cancels out).
- newgrad: company-an's Software Engineer New Grad posting -- squarely
  in-range fintech infrastructure work behind a junior title (judged 74
  before the 2026-09-02 profile edit, 50 after). Watches the one
  dimension the other three cannot: whether the judge has started
  scoring junior-scoped roles as beneath him.
"""

from __future__ import annotations

ANCHOR_PLATFORM = "anchor"

ANCHORS: list[dict] = [
    {
        "name": "high-fde-interview-truth",
        "job_title": "Forward Deployed Engineer",
        "company_slug": "company-m",
        "expect": (75, 100),
        "description_text": (
            "# company-m - Forward Deployed Engineer **Salary:** $150K - $215K **"
            "Equity:** Competitive equity **Location:** **Work Policy:** Hybrid, 4 da"
            "ys in-office in SF or NY **Visa Sponsorship:** Visa sponsorship availabl"
            "e ## Interview Process 1. Hiring manager screen (recruiting l"
            "ead) 2. Technical & behavioral interview (engineering manager)"
            " 3. Technical Coding Interview 4. Final Round \u2014 Meet the Team & Presenta"
            "tion ## About this role We're hiring Forward Deployed Engineers to own c"
            "ustomer deployments from technical build through go-live and optimizatio"
            "n. You'll be the technical owner for health system implementations, work"
            "ing directly with customers to build, configure, and deploy production A"
            "I agents tailored to their workflows. You'll work closely with Agent Pro"
            "duct Managers who own the customer relationship and project plan, while "
            "you own the technical execution\u2014building integrations, creating custom w"
            "orkflows, and ensuring successful launches. What You'll Do You'll be the"
            " technical force behind bringing AI agents to life for healthcare organi"
            "zations. This isn't a typical engineering role\u2014you'll work at the inters"
            "ection of cutting-edge AI, complex healthcare systems, and real customer"
            " impact. Ship Production AI Agents - Own implementations end-to-end for "
            "health systems, from technical scoping through go-live and beyond - Buil"
            "d custom integrations with complex healthcare platforms - Design intelli"
            "gent workflows tailored to each customer's specialty, patient population"
            ", and operational constraints - Launch agents that handle thousands of r"
            "eal patient interactions daily Solve Hard Technical Problems - Debug gna"
            "rly integration issues across phone systems, EHRs, scheduling platforms,"
            " and patient engagement tools - Architect creative solutions to handle h"
            "ealthcare's complexity\u2014insurance verification, appointment rules, clinic"
            "al protocols - Build tooling and automation that makes every future impl"
            "ementation faster and better - Optimize agent performance in production "
            "using real-world data and customer feedback Shape the Product - Partner "
            "directly with Product Engineering to influence platform direction based "
            "on what you learn in the field - Identify patterns across implementation"
            "s that become product features - Work with Sales to scope technical requ"
            "irements and demo capabilities to prospects - Be the voice of the custom"
            "er\u2014you'll see what works, what doesn't, and what customers actually need"
            " ## Tech stack Python, APIs, EHR/PMS Integrations, Twilio, AI/LLM, TypeS"
            "cript ## About the company - **Team size:** mid-size - **Founded:** recently - "
            "**Total funding:** substantial The company is building healthcare that impro"
            "ves. From call center automation and form intake to referrals, document"
            " processing, outreach, and payments, we manage the entire patient journe"
            "y for providers. Our platform is the most widely used AI agents platform"
            " for the patient journey, built on a large corpus of patient interactions acro"
            "ss many specialties and a model that handles the complexity general-purpos"
        ),
    },
    {
        "name": "mid-adjacent-sre",
        "job_title": "Senior DevOps/SRE Engineer",
        "company_slug": "company-f",
        "expect": (35, 70),
        "description_text": (
            "COMPANY\u00a0\n\ncompany-f is a mobile-first financial wellness platform des"
            "igned to help individuals take control of their financial future. We lev"
            "erage artificial intelligence to provide personalized insights and are b"
            "uilding a financial ecosystem by offering tools and services that provid"
            "e instant access to cash, and building credit. Our goal is to empower ev"
            "ery customer to achieve long-term financial stability.\n\n\nFounded in 2019"
            " by a repeat fintech f"
            "ounder whose prior company was acquired ("
            "2017). Venture-backed and led "
            "by industry pioneers from companies such as; PayPal, Square, and Cash Ap"
            "p, we are well positioned to build the future of inclusive finance throu"
            "gh cutting-edge technology and customer-centric solutions.\n\nOverview \n\nT"
            "he Platform team owns the infrastructure company-f runs on: the AWS e"
            "nvironment, deployments, observability, and the SOC 2 and PCI-DSS contro"
            "ls that keep customer financial data safe. We're also building the AI in"
            "frastructure our engineers use every day. When this team does its job we"
            "ll, engineers ship without worrying about what's underneath them and aud"
            "its find real controls instead of gaps.\n\nAs a Senior DevOps / SRE Engine"
            "er on this team, you'll own reliability and deployments across our AWS a"
            "nd Kubernetes environment, take real ownership of our compliance work, a"
            "nd help build the AI infrastructure and new product foundations we're in"
            "vesting in next. This is a rare chance to join a fast-growing fintech st"
            "artup where your work directly protects the systems our users depend on."
            "\n\n\n\n\nWhat You'll do \n\nCore reliability and infrastructure \n\n - Own relia"
            "bility, deployments, observability, and incident response across our AWS"
            " environment (Kubernetes/EKS, Datadog, CI/CD, networking), supporting ba"
            "ckend, mobile, data, ML, and AI engineering teams.\n\n - Maintain, optimiz"
            "e, and improve our AWS infrastructure footprint to align with industry b"
            "est standards and scalability requirements.\n\n - Own our infrastructure-a"
            "s-code stack (Terraform/OpenTofu). Hands-on AWS CDK experience is a stro"
            "ng plus.\n\n - Reduce our dependence on any single vendor by favoring open"
            " standards and swappable tooling where it makes sense.\n\nCompliance\n\n - T"
            "ake real, hands-on ownership of SOC 2 and PCI-DSS work. Not just documen"
            "ting controls, but building and operating the systems that make them gen"
            "uinely true in production.\n\n - Partner directly with our security functi"
            "on to close real gaps as they surface, including network segmentation an"
            "d traffic inspection.\n\nCI/CD and deployments\n\n - Make deploys and rollba"
            "cks easier, more predictable, and more consistent across the org.\n\n - Co"
            "nsolidate and speed up CI/CD pipelines that currently span multiple tool"
            "s and are evolving to support services in TypeScript/Node, Python, and K"
            "otlin as our engineering team\u2019s specialized use cases grow.\n\nAI infrastr"
            "ucture and product builds\n\n - Build and extend our internal AI infrastru"
        ),
    },
    {
        "name": "low-non-engineering",
        "job_title": "Senior Field Marketing Manager",
        "company_slug": "company-m",
        "expect": (0, 25),
        "description_text": (
            "# company-m - Senior Field Marketing Manager **Salary:** $150K - $20"
            "0K **Equity:** Competitive equity **Location:** **Work Policy:** 1-3 day"
            "s in-office in San Francisco, CA or New York, NY, OR remote **Visa Spons"
            "orship:** Visa sponsorship not available ## Interview Process 1. Recruit"
            "er Screen 2. Hiring Manager Interview 3. Live Case Exercise / Conversati"
            "onal Exercise 4. Onsite Interview ## About this role We are looking for "
            "a Senior Field Marketing Manager with 5\u20137 years of experience to bridge "
            "marketing and sales at one of the fastest-growing AI companies in health"
            "care. You'll be the first dedicated field marketing hire at the company"
            ", owning the strategy and execution of regional programs \u2014 executive di"
            "nners, customer experiences, roadshows, and more \u2014 that directly drive p"
            "ipeline and revenue. The company just closed a sizable Series B, has 100+ events"
            " planned this year, and is scaling across the entire healthcare industry"
            ". There is an existing foundation from a contractor, but you'll be build"
            "ing the in-house function from the ground up alongside a newly hired Hea"
            "d of Events & Field Marketing. What will you be doing? - Own end-to-end "
            "planning and execution of regional field marketing programs \u2014 executive "
            "dinners, customer events, roadshows, and specialty-specific activations "
            "\u2014 that generate measurable sales pipeline - Serve as the critical bridge"
            " between marketing and sales, guiding reps on marketing resources, manag"
            "ing pre-event bookings, and driving post-event follow-ups to keep pipeli"
            "ne moving - Build and maintain a feedback loop with sales to track pipel"
            "ine metrics, hold teams accountable, and continuously optimize program R"
            "OI - Develop repeatable, scalable field marketing processes and playbook"
            "s as the first in-house IC on this team - Report on event performance wi"
            "th strong data rigor \u2014 pipeline attribution, conversion rates, and ROI \u2014"
            " to inform strategy and budget allocation ---- Days 1\u201330 Immerse, Assess"
            " & Align - Understand the territories, pipeline gaps, and what \"great\" l"
            "ooks like at company-m Territory & Pipeline Intelligence - Embed with regio"
            "nal Sales and BD teams \u2014 join calls, attend pipeline reviews, and learn "
            "the language of each territory's accounts and ICP - Pull CRM data (Sales"
            "force/HubSpot) to map where pipeline is stalling by region, deal stage, "
            "and vertical \u2014 identify where field marketing can accelerate deals now -"
            " Identify the top 20\u201330 target accounts per region and understand which "
            "are event-receptive vs. digitally engaged - Review any prior field marke"
            "ting activity: event formats, attendance rates, meetings sourced, and pi"
            "peline influenced Cross-functional Relationship Building - Schedule stru"
            "ctured 1:1s with regional Sales leaders, BDRs, Demand Gen, Provider Succ"
            "ess, and Brand to align on expectations and establish communication cade"
            "nce - Understand the current vendor and agency landscape \u2014 who exists, w"
            "hat contracts are in place, what's worked and what hasn't - Learn company-m"
            "'s brand guidelines, event standards, and messaging framework from the B"
        ),
    },
    {
        # Added 2026-09-02 after a profile edit alone moved three junior-titled
        # postings 17-24 points down (docs/qualify.md, "Profile sensitivity").
        # None of the three anchors above is junior-titled, so that dimension
        # was invisible to them. This is a real new-grad posting at a fintech
        # infrastructure company (company-an), judged 74 before the edit and
        # 50 after; the band is where it belongs with the prompt's "scoped
        # below him costs nothing" rule in place.
        "name": "newgrad-in-range",
        "job_title": "Software Engineer New Grad",
        "company_slug": "company-an",
        "expect": (55, 90),
        "description_text": (
            "ABOUT THE COMPANY company-an is building the AI-native operating system "
            "for regulated finance, starting with mortgage servicing. We're a Series "
            "C company backed by a16z, transforming industries that others have writt"
            "en off as too complex to innovate. Rather than build on top of broken le"
            "gacy systems, we took a different approach: we built and operate our own"
            " mortgage servicing business managing $110+ billion in loans. This wasn'"
            "t the end goal, it was how we deeply understood the complexity needed to"
            " build software that actually works in regulated industries. The results"
            " speak for themselves. We've transformed mortgage servicing from a 0% ma"
            "rgin business into 60%+ margins while dramatically improving customer ex"
            "perience. Major enterprise contracts are now deploying across the indust"
            "ry. company-an OS is our unified platform that makes every process struc"
            "tured and programmable and it is perfectly positioned for the AI era. Wh"
            "en everything flows through one system with rich data, AI agents don't j"
            "ust automate tasks, they continuously improve entire operations. Mortgag"
            "e servicing is just the beginning of our vision to transform regulated i"
            "ndustries and beyond. ENGINEERING AT company-an Our engineering team is "
            "here to power the software ecosystem to disrupt one of the most outdated"
            " and regulated spaces in the financial sector - the mortgage industry. W"
            "e\u2019ve built out the foundation of a modern mortgage servicing platform th"
            "at can accurately handle billions of dollars at scale, but we\u2019re just ge"
            "tting started. At company-an, we want you to do the best work of your li"
            "fe. You'll be surrounded by a tight-knit community of exceptional people"
            " from places like Stripe, Jane Street, Meta, and Google who care deeply "
            "about their work and each other. Our problem space is complex, but you\u2019l"
            "l get a lot of autonomy so that you can learn quickly, execute effective"
            "ly, and deliver the highest level of business impact. Our tech stack is "
            "built on Python, React, Docker, Kubernetes, and Google Cloud Platform. O"
            "ur office locations are based in New York City and in San Francisco. RES"
            "PONSIBILITIES - Learn new concepts and technologies quickly and apply th"
            "em to challenging problems - Design and build robust and extensible infr"
            "astructure to handle evolving and complex federal, state, and agency reg"
            "ulations - Dive deep to understand the inner workings of a highly comple"
            "x industry - Work with Product and Design to define the best experience "
            "for our customers IDEAL BACKGROUND - 0-1 year of software engineering ex"
            "perience building quality software applications at scale - Bachelor's de"
            "gree in Computer Science or related field - Strong communicator. Able to"
            " work cross-functionally to balance product and technical requirements -"
            " Great at building scalable systems from scratch with a fast turnaround "
            "- Experience with one or more of the following: web application developm"
            "ent, mobile application development, building large-scale distributed sy"
            "stems, or infrastructure management - Time manag"
        ),
    },
]
