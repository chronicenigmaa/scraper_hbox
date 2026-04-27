"""
scraper.py — scraping + scoring logic
LinkedIn / Reddit / Twitter via SerpApi + OpenAI scoring
"""

import time, json, re, requests
from openai import OpenAI
from datetime import datetime, timezone

# ── Queries ───────────────────────────────────────────────────────
QUERIES = {
    "LinkedIn": [
        'site:linkedin.com/posts "want to build an app" -hiring -job',
        'site:linkedin.com/posts "have an app idea" -hiring -job',
        'site:linkedin.com/posts "need someone to build an app" -hiring -job',
        'site:linkedin.com/posts "looking for someone to build" app -hiring -job',
        'site:linkedin.com/posts "want to turn my idea into an app" -hiring',
        'site:linkedin.com/posts "need an app built" -hiring -job',
        'site:linkedin.com/posts "build a mobile app" "looking for" -hiring',
        'site:linkedin.com/posts "app idea" "looking for a developer" -hiring',
        'site:linkedin.com/posts "need to build" "mobile app" startup -hiring',
        'site:linkedin.com/posts "app development" "who can help" OR "recommendations" -hiring',
    ],
    "Reddit": [
        'site:reddit.com/r/entrepreneur "want to build an app" OR "have an app idea"',
        'site:reddit.com/r/startups "looking for developer" app -hiring',
        'site:reddit.com/r/SaaS "need someone to build" app -hiring',
        'site:reddit.com/r/Entrepreneur "app idea" "how do I" OR "where do I find"',
        'site:reddit.com/r/smallbusiness "build a mobile app" "how much" OR "who can"',
        'site:reddit.com/r/forhire "need app built" OR "app idea" -"looking to hire" developer',
        'site:reddit.com/r/startups "MVP" "mobile app" "looking for" -hiring',
        'site:reddit.com/r/androiddev "build my app idea" OR "find a developer"',
    ],
    "Twitter/X": [
        'site:twitter.com OR site:x.com "want to build an app" "looking for" -hiring -job',
        'site:twitter.com OR site:x.com "have an app idea" "need a developer" -hiring',
        'site:twitter.com OR site:x.com "need someone to build my app" -hiring',
        'site:twitter.com OR site:x.com "app idea" "who can build" OR "anyone can build" -hiring',
        'site:twitter.com OR site:x.com "build a mobile app" "recommendations" -hiring',
    ],
}

SCORE_PROMPT = """\
You score leads for a mobile app development agency that builds apps for clients.

We WANT: people who have an app idea, want an app built for their business,
are looking for a dev team or agency, asking for recommendations.

We DO NOT WANT: job postings, people hiring in-house devs, developers seeking work,
tutorials, app launch announcements, generic tech discussion.

SOURCE: {source}
POST: "{text}"
AUTHOR: {name}

Return ONLY valid JSON, no markdown:
{{
  "score": <integer 1-10>,
  "tier": <"HOT"|"WARM"|"COLD">,
  "intent": <"explicit"|"strong"|"moderate"|"weak">,
  "urgency": <"immediate"|"soon"|"exploring"|"none">,
  "reason": "<one sentence>"
}}

9-10 HOT  = has a specific app idea, actively looking for someone to build it, ready to move
7-8  HOT  = clear need, entrepreneur or business owner context, some urgency
5-6  WARM = interested in having an app built, early stage, asking general questions
1-4  COLD = in-house hire, already launched, tutorial, job ad, no buying intent"""


# ── Name extractors ───────────────────────────────────────────────
def _li_name(url):
    m = re.search(r"linkedin\.com/posts/([^_?/]+)", url)
    if not m: return ""
    slug = m.group(1)
    # Remove trailing numeric IDs and common suffixes
    slug = re.sub(r"-(post|activity|update|article)$", "", slug)
    slug = re.sub(r"-\d{4,}$", "", slug)
    name = slug.replace("-", " ").strip()
    # Discard if too long or clearly not a name
    if len(name.split()) > 5 or len(name) > 40:
        return ""
    return name.title()

def _reddit_name(url):
    m = re.search(r"reddit\.com/(?:user|u)/([^/?\s]+)", url)
    return f"u/{m.group(1)}" if m else ""

def _twitter_handle(url):
    m = re.search(r"(?:twitter|x)\.com/([^/?#\s]+)", url)
    if not m: return ""
    h = m.group(1)
    return "" if h in ("search","hashtag","i","intent","home","explore") else f"@{h}"

NAME_FN = {
    "LinkedIn":  _li_name,
    "Reddit":    _reddit_name,
    "Twitter/X": _twitter_handle,
}

URL_FILTER = {
    "LinkedIn":  lambda u: "linkedin.com/posts" in u,
    "Reddit":    lambda u: "reddit.com/r/" in u or "reddit.com/comments" in u,
    "Twitter/X": lambda u: "twitter.com" in u or "x.com" in u,
}


# ── SerpApi search ────────────────────────────────────────────────
def _serp(query, key):
    try:
        r = requests.get("https://serpapi.com/search", params={
            "api_key": key, "engine": "google", "q": query,
            "num": 10, "tbs": "qdr:m6", "hl": "en", "gl": "us",
        }, timeout=15)
        if r.status_code == 429:
            raise RuntimeError("QUOTA_EXCEEDED")
        r.raise_for_status()
        return r.json().get("organic_results", [])
    except RuntimeError:
        raise
    except Exception:
        return []



# ── Fetch full post text where possible ──────────────────────────
def _fetch_reddit_text(url):
    """Fetch full selftext from Reddit JSON API."""
    try:
        json_url = url.rstrip("/") + ".json"
        r = requests.get(json_url, headers={"User-Agent": "LeadGenBot/1.0"}, timeout=8)
        data = r.json()
        selftext = data[0]["data"]["children"][0]["data"].get("selftext", "")
        title    = data[0]["data"]["children"][0]["data"].get("title", "")
        if selftext and selftext not in ("[removed]", "[deleted]"):
            return f"{title}\n\n{selftext}".strip()
        return title
    except Exception:
        return ""


def _fetch_full_text(source, url, fallback):
    if source == "Reddit":
        full = _fetch_reddit_text(url)
        return full if len(full) > len(fallback) else fallback
    return fallback


# ── Public API ────────────────────────────────────────────────────
def scrape_all(serpapi_key, platforms, on_query=None, custom_keywords=None):
    """
    Scrape selected platforms. on_query(source, done, total) called each query.
    custom_keywords: list of extra search terms added on top of defaults.
    Returns list of raw post dicts.
    Raises RuntimeError("QUOTA_EXCEEDED") if SerpApi limit hit.
    """
    seen, posts = set(), []

    for source in platforms:
        # Build query list: defaults + any custom keywords
        queries = list(QUERIES[source])
        if custom_keywords:
            site_prefix = {
                "LinkedIn":  "site:linkedin.com/posts",
                "Reddit":    "site:reddit.com/r/entrepreneur OR site:reddit.com/r/startups",
                "Twitter/X": "site:twitter.com OR site:x.com",
            }.get(source, "")
            for kw in custom_keywords:
                queries.append(f'{site_prefix} "{kw}" -hiring -job')

        for i, q in enumerate(queries):
            if on_query:
                on_query(source, i, len(queries))
            items = _serp(q, serpapi_key)   # may raise QUOTA_EXCEEDED
            for item in items:
                url = item.get("link", "")
                if not url or url in seen or not URL_FILTER[source](url):
                    continue
                seen.add(url)
                text = re.sub(r"\s+", " ",
                    f"{item.get('title','')} {item.get('snippet','')}").strip()
                if len(text) < 20:
                    continue

                # Try to get real name from Google title e.g. "Ritesh Verma's Post | LinkedIn"
                raw_title = item.get("title", "")
                name_from_title = ""
                title_match = re.match(r"^(.+?)(?:'s Post|'s post| - Post| \|)", raw_title)
                if title_match:
                    candidate = title_match.group(1).strip()
                    # Accept if it looks like a name (2-5 words, no special chars)
                    if 1 < len(candidate.split()) <= 5 and re.match(r"^[A-Za-z\s\.\-]+$", candidate):
                        name_from_title = candidate

                name = name_from_title or NAME_FN[source](url)

                # Strip "X's Post" prefix since we show name separately
                text = re.sub(r"^[A-Za-z\s\.\-]+'s [Pp]ost[\s\|:·\-]*", "", text).strip()

                # Try to fetch the full post text (works for Reddit)
                full_text = _fetch_full_text(source, url, text)

                posts.append({
                    "source":    source,
                    "text":      full_text,
                    "post_url":  url,
                    "post_date": item.get("date", ""),
                    "name":      name,
                })
            time.sleep(0.3)

    return posts


def score_all(posts, openai_key, model="gpt-4o-mini", on_score=None):
    """
    Score posts. on_score(done, total, name) called each post.
    Returns enriched, sorted list.
    """
    client = OpenAI(api_key=openai_key)
    results = []

    for i, post in enumerate(posts):
        if on_score:
            on_score(i, len(posts), post.get("name", ""))

        prompt = SCORE_PROMPT.format(
            source=post["source"],
            text=post["text"][:700],
            name=post.get("name") or "Unknown",
        )
        try:
            resp = client.chat.completions.create(
                model=model, max_tokens=180, temperature=0,
                messages=[
                    {"role": "system", "content": "Return valid JSON only."},
                    {"role": "user",   "content": prompt},
                ],
            )
            raw = re.sub(r"```json|```", "",
                         resp.choices[0].message.content).strip()
            s = json.loads(raw)
        except Exception:
            s = {"score":0,"tier":"COLD","intent":"weak","urgency":"none","reason":"Scoring error"}

        results.append({
            "source":    post["source"],
            "tier":      s.get("tier", "COLD"),
            "score":     int(s.get("score", 0)),
            "intent":    s.get("intent", "weak"),
            "urgency":   s.get("urgency", "none"),
            "reason":    s.get("reason", ""),
            "name":      post.get("name", ""),
            "post_url":  post["post_url"],
            "post_date": post.get("post_date", ""),
            "post_text": post["text"],  # full text
            "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        })
        time.sleep(0.2)

    tier_order = {"HOT": 0, "WARM": 1, "COLD": 2}
    results.sort(key=lambda x: (tier_order.get(x["tier"], 3), -x["score"]))
    return results