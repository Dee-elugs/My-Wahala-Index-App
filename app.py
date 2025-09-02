
import streamlit as st
import requests
from bs4 import BeautifulSoup
from openai import OpenAI

import pandas as pd
from datetime import date
from pathlib import Path
import re
import random
from collections import Counter

st.set_page_config(page_title="Wahala Index", page_icon="🔥", layout="wide")

# --- Simple storage config ---
HISTORY_CSV = Path("wahala_history.csv")
CAPTION_LOG_CSV = Path("wahala_caption_log.csv")  # track today's used summaries to avoid repeats

# ────────────────────────────────────────────────────────────────────────────────
# Sources & Categories
# ────────────────────────────────────────────────────────────────────────────────
SOURCES = {
    "Punch": "https://punchng.com",
    "Vanguard": "https://www.vanguardngr.com/",
    "Premium Times": "https://www.premiumtimesng.com/",
    "Daily Trust": "https://dailytrust.com/",
    "TheCable": "https://www.thecable.ng/",
    "Naija News": "https://www.naijanews.com/",
}

CATEGORIES = {
    "Politics": ["senate", "president", "governor", "minister", "election", "assembly", "bill", "policy", "pdp", "apc", "inec"],
    "Economy": ["inflation", "naira", "forex", "subsidy", "customs", "tax", "unemployment", "budget", "cbn"],
    "Power & Fuel": ["fuel", "petrol", "diesel", "pump price", "nepa", "electricity", "power", "grid", "outage"],
    "Security": ["bandit", "kidnap", "attack", "security", "police", "insurgent", "boko haram", "theft", "derail", "train"],
    "Social Buzz": ["twitter", "x.com", "controversy", "trend", "backlash", "viral", "protest", "strike", "nlc", "asuu", "tuc"],
}

# tiny English stopwords to clean topics (no external libs)
STOPWORDS = set("""
a an and the is are was were be been being of to in for on at by with from up down over under into out
as about after before during around across between against through while due per via
i you he she it we they them us our your their my his her its this that these those
will would can could shall should may might must not no yes do does did doing done
than then there here where who whom whose which what why how
new govt government nigeria nigerian lagos abuja kano state federal local today news
""".split())

# EXTRA: drop junk tokens from topics; keep acronyms correct
BAD_TOPIC_TOKENS = set("""
says said say saying tells told urges reacts vows meets seeks slams backs hails warns admits alleges claims
video photos picture watch live update updates headline headlines report reports story stories breaking latest
south north east west centre central statewide state-wide nationwide
""".split())

BRAND_BLOCKLIST = {
    "blockdag","pi network","worldcoin","shiba","dogecoin","pepe","safemoon","airdrop","presale","token sale"
}

ACRONYM_UPCASE = {
    "cbn":"CBN","efcc":"EFCC","nnpc":"NNPC","fg":"FG","nlng":"NLNG","imf":"IMF","opec":"OPEC",
    "pdp":"PDP","apc":"APC","inec":"INEC","nlc":"NLC","tuc":"TUC","asuu":"ASUU"
}
SMALL_WORDS = {"and", "or", "the", "of", "in", "on", "at", "to", "for", "by", "with"}

# Headlines cleaner: strip site prefixes & glued words, drop clickbait/crypto-promo
HEADLINE_PREFIXES = (
    "naija news", "nigeria news", "premium times", "thecable", "daily trust", "vanguard", "punch"
)

def _split_camel_glue(s: str) -> str:
    # e.g., "NewsWhy" -> "News Why", "VisaControversy" -> "Visa Controversy"
    return re.sub(r'([a-z])([A-Z])', r'\1 \2', s)

def _normalize_headline(h: str) -> str:
    h = _split_camel_glue(h)
    h = re.sub(r"\s+", " ", h).strip()
    # remove "<prefix>[:–-] " at start, case-insensitive
    low = h.lower()
    for pref in HEADLINE_PREFIXES:
        m = re.match(rf"^{re.escape(pref)}\s*([:–-])\s*", low, flags=re.IGNORECASE)
        if m:
            h = h[len(m.group(0)):]
            low = h.lower()
            break
        if low.startswith(pref + " "):
            h = h[len(pref) + 1:]
            low = h.lower()
            break
    # unify punctuation
    h = h.replace("—", "–").replace("…", " ")
    h = re.sub(r"\s*,\s*,+", ", ", h)
    return h.strip()

def _is_bad_headline(h: str) -> bool:
    low = h.lower()
    if any(b in low for b in ("sponsored", "advert", "advertorial", "promo", "brand studio", "press release")):
        return True
    if any(b in low for b in BRAND_BLOCKLIST):
        return True
    # too long or too many commas looks like SEO-chain
    if len(h.split()) > 20 or low.count(",") >= 3:
        return True
    # contains too many ALLCAPS tokens (promo-ish)
    if sum(1 for w in h.split() if len(w) >= 3 and w.isupper()) >= 3:
        return True
    return False

# ────────────────────────────────────────────────────────────────────────────────
# Setup LLM (OpenRouter via OpenAI client) — used ONLY for the numeric score
# ────────────────────────────────────────────────────────────────────────────────
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=st.secrets.get("OPENROUTER_API_KEY", "your-openrouter-api-key"),
)

# ────────────────────────────────────────────────────────────────────────────────
# Titles per tone (caption summary is generated below; tip is separate)
# ────────────────────────────────────────────────────────────────────────────────
CAPTION_VARIANTS = {
    "Classic": {
        "1": {"titles": ["😌 1 — Peace Mode Activated", "😌 1 — Cruise Control"]},
        "2": {"titles": ["🙂 2 — Small Small Wahala", "🙂 2 — Minor Turbulence"]},
        "3": {"titles": ["😬 3 — Vibes Are Shaky", "😬 3 — Middle of the Storm"]},
        "4": {"titles": ["😫 4 — High Alert Vibes", "😫 4 — Wahala Rising"]},
        "5": {"titles": ["🔥 5 — Wahala With Full Chest", "🔥 5 — Maximum Chaos"]},
    },
    "Gen-Z": {
        "1": {"titles": ["😌 1 — Soft Life Loading", "😌 1 — Chillaxation"]},
        "2": {"titles": ["🙂 2 — Tiny Wahala", "🙂 2 — Light Waka"]},
        "3": {"titles": ["😬 3 — Vibes are ‘Ehn Ehn’", "😬 3 — Mid Wahala"]},
        "4": {"titles": ["😫 4 — Wahala Dey Ramp Up", "😫 4 — No Loose Guard"]},
        "5": {"titles": ["🔥 5 — Full Blown Gbege", "🔥 5 — Chaos Supreme"]},
    },
    "Pidgin": {
        "1": {"titles": ["😌 1 — Everywhere Steady", "😌 1 — Soft Day"]},
        "2": {"titles": ["🙂 2 — Small Wahala", "🙂 2 — E Still Manage"]},
        "3": {"titles": ["😬 3 — E Dey Balance So-So", "😬 3 — Watch & Move"]},
        "4": {"titles": ["😫 4 — E Don Dey Hot", "😫 4 — High Tension"]},
        "5": {"titles": ["🔥 5 — Wahala Full Ground", "🔥 5 — No Try Am"]},
    },
}

# Memes (kept as you set them)
MEME_GIFS = {
    "1": [
        "https://media.giphy.com/media/l0HlA0x8BoK8PtGlO/giphy.gif",
        "https://media.giphy.com/media/111ebonMs90YLu/giphy.gif",
    ],
    "2": [
        "https://media2.giphy.com/media/v1.Y2lkPTc5MGI3NjExcmN4OXljM2g4ZWsxdXViZnA4aXF1OGRpbXR0dnNydHpmbDk3NjR6NCZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/fCmnDUmpNYqnE2PidN/giphy.gif",
        "https://media4.giphy.com/media/v1.Y2lkPTc5MGI3NjExOXV4bW56OHR0aWU0Y3FyMzJscmVkNTczbm80ZTJ6NHhldzhpZ3B0aCZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/6doc3WcKb9o3OUwfzf/giphy.gif",
        "https://media3.giphy.com/media/v1.Y2lkPTc5MGI3NjExZXR4OHI0dDVtNnV3cmU2cnR4OWV1Y3JpazJnd2pzbHJteHZtYTVyaiZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/QLKSt3wQqlj7a/giphy.gif",
    ],
    "3": [
        "https://media4.giphy.com/media/v1.Y2lkPTc5MGI3NjExdTlrM2tod3oyYWlqZWRpc3lzZGJzYnBqcTVpOGlxY2NnOXBuYmR6MiZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/aGyMTA2jXy6o5pc6L7/giphy.gif",
        "https://media2.giphy.com/media/v1.Y2lkPTc5MGI3NjExYXFtcGFxeG43MjRlM21wNnRyYW5ydTlpaWE4YTU3aGoxNHhwNWIweiZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/X8vcDprHF2PC5tNN1E/giphy.gif",
        "https://media1.giphy.com/media/v1.Y2lkPTc5MGI3NjExYW5odmV2d2dheDM0dDYwZzVmbmtlMnZhNWFrZjRkbHhsbmk3a3lqYiZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/zIZldEXyyo64qOIgvb/giphy.gif",
        "https://media0.giphy.com/media/v1.Y2lkPTc5MGI3NjExbXJlYTNtaGVxam5oeGFmY2Zjd2VlcDRkOWVvOWlidnliMXRpNWR6eiZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/dKJQWJA0RerYVcOM7l/giphy.gif",
        "https://media0.giphy.com/media/v1.Y2lkPTc5MGI3NjExNGVjbWd3YXF6dG5vdjhwb2U4NnV6cnV2a3Z1MzZyb2MydGptOXU3bSZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/3o6Mb62DMFBELvevi8/giphy.gif",
    ],
    "4": [
        "https://media4.giphy.com/media/v1.Y2lkPTc5MGI3NjExbHQ1amUxNTE1b3N0eXo2ZHNkMHJzd3lpM3k0N3o5ZTJ2cHhyYm81cSZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/0yXjVvEM2VWUX2CWpE/giphy.gif",
        "https://media1.giphy.com/media/v1.Y2lkPTc5MGI3NjExcWxtaG45czBvNjBwa25md2E3dGZscmxvZGQyMXdtem85YnZtdThobCZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/BJEGePnDfDemfw7SQV/giphy.gif",
        "https://media2.giphy.com/media/v1.Y2lkPTc5MGI3NjExY3Bna3J5ZjkzcGczdWcydnlnYXBtYWV0NGoyb3JkaTdmc2sxNmxnNCZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/2UlW42qqNY9udwlOke/giphy.gif",
    ],
    "5": [
        "https://media1.giphy.com/media/v1.Y2lkPTc5MGI3NjExMjJ4cmo4eHJ2ZmI0enQ1MjBwZ25oeXQ1NDh3aTVldGs2bGk0djNsbyZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/nrXif9YExO9EI/giphy.gif",
        "https://media4.giphy.com/media/v1.Y2lkPTc5MGI3NjExZHJqOW0xYXRtdmNsenN4MG8wZ3Y1YnAwcjBseGxleHpnbmZwbzYyZSZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/0Wzkc9iirQ4ZI7JoaD/giphy.gif",
    ],
}

def pick_title(score_int: int, tone: str):
    tone = tone if tone in CAPTION_VARIANTS else "Classic"
    key = str(score_int)
    titles = CAPTION_VARIANTS.get(tone, {}).get(key, {}).get("titles", [])
    return random.choice(titles) if titles else f"{key} — Wahala Level"

def pick_gif(score_int: int):
    urls = MEME_GIFS.get(str(score_int), [])
    return random.choice(urls) if urls else None

# Sidebar
with st.sidebar:
    st.markdown("#### About")
    st.markdown("Multi-source headlines • Clean summary + tone tip • Trend & categories")
    st.markdown("---")
    st.markdown("#### Vibes Settings")
    tone = st.selectbox("Tone", ["Classic", "Gen-Z", "Pidgin"], index=1)
    show_meme = st.checkbox("Show meme/GIF for today's score", value=True)

# ────────────────────────────────────────────────────────────────────────────────
# Scrapers
# ────────────────────────────────────────────────────────────────────────────────
def fetch_headlines_from(url: str, min_len: int = 10, max_words: int = 20, limit: int = 40):
    """Fetch headlines generically from a URL (tries h1/h2/h3/a). De-duped, cleaned, and filtered."""
    try:
        html = requests.get(url, timeout=10).text
        soup = BeautifulSoup(html, "html.parser")
        tags = soup.find_all(["h1", "h2", "h3", "a"])
        texts = []
        for t in tags:
            raw = (t.get_text(strip=True) or "").strip()
            if not raw:
                continue
            raw = _normalize_headline(raw)
            if _is_bad_headline(raw):
                continue
            if len(raw) >= min_len and 3 <= len(raw.split()) <= max_words:
                texts.append(raw)

        # De-duplicate (case/punct-insensitive)
        clean, seen = [], set()
        for t in texts:
            key = re.sub(r"\W+", " ", t.lower()).strip()
            if key not in seen:
                seen.add(key)
                clean.append(t)
        return clean[:limit]
    except Exception as e:
        st.warning(f"Couldn’t fetch from {url}: {e}")
        return []

def fetch_all_sources():
    return {name: fetch_headlines_from(url) for name, url in SOURCES.items()}

# Prompt builder (for numeric score only)
def build_prompt_from_headlines(headlines):
    prompt = (
        "Put on your cap, Unofficial Wahala Detector 🧢 — these headlines just dropped. "
        "What’s the gbege level? 1 (soft) to 5 (wahala pro max) 🔥\n\n"
    )
    for i, h in enumerate(headlines, 1):
        prompt += f"{i}. {h}\n"
    prompt += "\nReturn only a number from 1 to 5."
    return prompt

# LLM score
def get_wahala_score_from_llm(prompt):
    try:
        completion = client.chat.completions.create(
            model="qwen/qwen3-coder:free",
            messages=[
                {"role": "system", "content": "You predict a single digit (1-5). Output exactly one character with no text."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        return f"Error: {e}"

# history helpers
def _load_history() -> pd.DataFrame:
    if HISTORY_CSV.exists():
        try:
            df = pd.read_csv(HISTORY_CSV, dtype={"date": str, "wahala_score": int})
            return df
        except Exception:
            return pd.DataFrame(columns=["date", "wahala_score"])
    return pd.DataFrame(columns=["date", "wahala_score"])

def _save_today(score_int: int):
    today_str = date.today().isoformat()
    df = _load_history()
    df = df[df["date"] != today_str]
    df = pd.concat([df, pd.DataFrame([{"date": today_str, "wahala_score": score_int}])], ignore_index=True)
    df.to_csv(HISTORY_CSV, index=False)
    return df

def _parse_first_digit_1_to_5(text: str):
    m = re.search(r"[1-5]", text or "")
    return int(m.group()) if m else None

# categories
def score_categories(all_headlines):
    counts = {k: 0 for k in CATEGORIES}
    lower_lines = [h.lower() for h in all_headlines]
    for cat, kws in CATEGORIES.items():
        for h in lower_lines:
            if any(kw in h for kw in kws):
                counts[cat] += 1
    max_hits = max(counts.values()) if counts else 0
    scores = {cat: (1 if max_hits == 0 else max(1, round(5 * (c / max_hits)))) for cat, c in counts.items()}
    return scores, counts

# ────────────────────────────────────────────────────────────────────────────────
# Clean topic extraction + clear summary sentence + tone-specific tip
# ────────────────────────────────────────────────────────────────────────────────
def _tokens(text: str):
    return [w.lower() for w in re.findall(r"[a-zA-Z][a-zA-Z\-]+", text)]

def _is_bad_topic(t: str) -> bool:
    tl = t.lower()
    if tl in BAD_TOPIC_TOKENS or tl in STOPWORDS:
        return True
    if any(b in tl for b in BRAND_BLOCKLIST):
        return True
    if len(tl) < 4:
        return True
    if tl in {"south", "north", "east", "west"}:
        return True
    if len(tl.split()) > 6 or any(len(x) > 18 for x in tl.split()):
        return True
    return False

def _nice_title(word: str) -> str:
    w = word
    if "-" in w and all(part.isalpha() for part in w.split("-")):
        w = w.replace("-", "–")
    parts = re.split(r"(\s+|–|-)", w)
    out = []
    for p in parts:
        if p.strip() == "":
            out.append(p)
        else:
            low = p.lower()
            if low in ACRONYM_UPCASE:
                out.append(ACRONYM_UPCASE[low])
            elif low in SMALL_WORDS:
                out.append(low)
            else:
                out.append(p[:1].upper() + p[1:])
    return "".join(out)

def _tidy_topic(t: str) -> str:
    s = t.strip()
    s = re.sub(r"^(why|how|when|what|as|amid|after|before|during)\b[\s:,-]*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(says|urges|vows|warns|backs|hails|reacts|alleges|claims)\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(19|20)\d{2}\b", "", s)
    s = re.sub(r"\b(PDP)\s*,\s*(APC)\b", r"\1/\2", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*,\s*,+", ", ", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,;:—–-")
    s = _nice_title(s)
    return s

def _clean_topics(raw_topics):
    clean = []
    for t in raw_topics:
        if _is_bad_topic(t):
            continue
        if len(t.replace(" ", "")) < 4:
            continue
        t2 = _tidy_topic(t)
        if not t2 or len(t2) < 4:
            continue
        clean.append(t2)
    # de-dup by lowercase
    seen, dedup = set(), []
    for t in clean:
        k = t.lower().strip()
        if k not in seen:
            seen.add(k)
            dedup.append(t)
    return dedup

def extract_topics(headlines, top_n=12):
    words = []
    for h in headlines:
        toks = _tokens(h)
        bigrams = [" ".join([toks[i], toks[i+1]]) for i in range(len(toks)-1)]
        words.extend([w for w in toks if w not in STOPWORDS and len(w) >= 4])
        for bg in bigrams:
            a, b = bg.split()
            if a not in STOPWORDS and b not in STOPWORDS:
                words.append(bg)
    counts = Counter(words)
    for k in list(counts.keys()):
        if " " in k:
            counts[k] = int(counts[k] * 1.3)  # favour bigrams
    raw_top = [w for w, _ in counts.most_common(top_n * 2)]
    top = _clean_topics(raw_top)
    # prefer multi-word phrases first
    multis = [t for t in top if " " in t]
    singles = [t for t in top if " " not in t]
    final = multis[: min(2, len(multis))] + singles[: max(0, 3 - min(2, len(multis)))]
    # pad if less than 2
    if len(final) < 2:
        final = (multis + singles)[:2]
    return final[:3]

def format_topics_sentence(topics):
    """Join topics as 'A, B and C'."""
    topics = [t for t in topics if t]
    if not topics:
        return "key issues"
    if len(topics) == 1:
        return topics[0]
    if len(topics) == 2:
        return f"{topics[0]} and {topics[1]}"
    return f"{topics[0]}, {topics[1]} and {topics[2]}"

# Tone-specific tiny templates (deterministic, short)
CLASSIC_SUMMARY_TEMPLATES = [
    "Top stories: {topics} lead today.",
    "{topics} dominate the headlines.",
    "Today’s cycle is driven by {topics}.",
]

GENZ_SUMMARY_TEMPLATES = [
    "Top stories: {topics} — TL is hot. 🔥",
    "Heads up: {topics}; low-key tense. 💫",
    "{topics} running the TL today. ⚡",
]

PIDGIN_SUMMARY_TEMPLATES = [
    "Main gist: {topics} dey lead today.",
    "Na {topics} dey top gist.",
    "Today matter na {topics}.",
]

CLOSERS = {
    "Classic": {
        1: "Light day — handle errands.",
        2: "Small bumps; add buffer time.",
        3: "Keep powerbank handy; move with sense.",
        4: "Batch errands; hold cash and data.",
        5: "Survival mode; postpone stress.",
    },
    "Gen-Z": {
        1: "Chill day — soft cruise. ✨",
        2: "Minor bumps; keep it pushing. 💫",
        3: "Eyes open, powerbank on deck. ⚡",
        4: "High tension — plan your waka. 🔥",
        5: "Hard day — conserve energy, fr. 💀",
    },
    "Pidgin": {
        1: "Day soft — no wahala.",
        2: "Small bumps — add small buffer, abeg.",
        3: "Shine eye, powerbank ready.",
        4: "High tension — batch waka; hold cash/data.",
        5: "Survival things — conserve energy.",
    },
}

def build_summary_variants(all_headlines, tone: str):
    topics = extract_topics(all_headlines, top_n=12)
    topic_sentence = format_topics_sentence(topics)
    if tone == "Gen-Z":
        temps = GENZ_SUMMARY_TEMPLATES
    elif tone == "Pidgin":
        temps = PIDGIN_SUMMARY_TEMPLATES
    else:
        temps = CLASSIC_SUMMARY_TEMPLATES
    variants = [t.format(topics=topic_sentence) for t in temps]
    # de-dup short variants
    seen, out = set(), []
    for v in variants:
        if v not in seen:
            out.append(v)
            seen.add(v)
    random.shuffle(out)
    return out

def pick_unused_today(captions, score: int):
    today_str = date.today().isoformat()
    if CAPTION_LOG_CSV.exists():
        try:
            log = pd.read_csv(CAPTION_LOG_CSV)
        except Exception:
            log = pd.DataFrame(columns=["date", "score", "caption"])
    else:
        log = pd.DataFrame(columns=["date", "score", "caption"])
    used = set(log[(log["date"] == today_str) & (log["score"] == score)]["caption"].tolist())
    for c in captions:
        if c not in used:
            log = pd.concat([log, pd.DataFrame([{"date": today_str, "score": score, "caption": c}])], ignore_index=True)
            log.to_csv(CAPTION_LOG_CSV, index=False)
            return c
    return captions[0] if captions else "Top stories: key issues."

def pick_closer(tone: str, score: int) -> str:
    return CLOSERS.get(tone, CLOSERS["Classic"]).get(score, "")

# ────────────────────────────────────────────────────────────────────────────────
# UI
# ────────────────────────────────────────────────────────────────────────────────
st.title(":orange[Naija] Wahala Index")
st.markdown("Let’s see how chaotic the country feels today — **clean summary** + **clear tone tip** 👀")

# Run on click
if st.button("Analyze Today's Wahala Level"):
    st.markdown("#")
    with st.spinner("Gathering headlines from multiple sources and invoking the Wahala Oracle..."):
        source_map = fetch_all_sources()
        all_headlines = [h for lst in source_map.values() for h in lst]

        if len(all_headlines) >= 10:
            # Overall score via LLM (still single digit)
            prompt = build_prompt_from_headlines(all_headlines[:60])
            wahala_score_text = get_wahala_score_from_llm(prompt)
            wahala_num = _parse_first_digit_1_to_5(wahala_score_text)

            # Title + clean summary + separate tone tip
            if wahala_num:
                title = pick_title(wahala_num, tone)
                summary_options = build_summary_variants(all_headlines, tone)
                summary = pick_unused_today(summary_options, wahala_num)
                tip = pick_closer(tone, wahala_num)
            else:
                title = "🧠 Oracle Confused"
                summary = "Couldn’t decode the vibes from today’s headlines."
                tip = "Try again soon."

            # Categories
            cat_scores, cat_hits = score_categories(all_headlines)

            # History + metrics
            if wahala_num:
                hist_df = _save_today(wahala_num)

                st.markdown("### Today’s Reading")
                col1, col2 = st.columns(2)
                with col1:
                    st.metric(label="Wahala Index (AI)", value=str(wahala_num))
                with col2:
                    df_sorted = hist_df.sort_values("date")
                    prev_rows = df_sorted[df_sorted["date"] < date.today().isoformat()]
                    if not prev_rows.empty:
                        prev_val = int(prev_rows.iloc[-1]["wahala_score"])
                        delta = wahala_num - prev_val
                        sign = "+" if delta > 0 else ""
                        st.metric("Change vs Yesterday", f"{sign}{delta}")
                    else:
                        st.metric("Change vs Yesterday", "—")

                left, right = st.columns([1.3, 1])
                with left:
                    st.subheader("7-Day Trend")
                    if not hist_df.empty:
                        trend_df = hist_df.sort_values("date").tail(7).set_index("date")[["wahala_score"]]
                        trend_df.rename(columns={"wahala_score": "AI Wahala"}, inplace=True)
                        st.line_chart(trend_df, use_container_width=True)
                    else:
                        st.caption("No history yet.")
                with right:
                    st.subheader("Category Heat (1–5)")
                    cat_df = pd.DataFrame({
                        "Category": list(cat_scores.keys()),
                        "Score": list(cat_scores.values()),
                        "Hits": [cat_hits[k] for k in cat_scores.keys()],
                    })
                    st.bar_chart(cat_df.set_index("Category")[["Score"]], use_container_width=True)
                    with st.expander("Keyword hits by category"):
                        st.dataframe(cat_df.set_index("Category"), use_container_width=True)

            # Vibe + meme
            st.markdown("#")
            st.markdown(f"## {title}")
            # ⬇️ summary vs tip are visually distinct
            st.markdown(f"#### {summary}")
            if tip:
                st.caption(tip)

            if show_meme and wahala_num:
                gif_url = pick_gif(wahala_num)
                if gif_url:
                    st.image(gif_url, caption="Meme of the day", use_container_width=True)
                else:
                    st.caption("Add your favourite meme links in MEME_GIFS to spice it up even more.")

            # Sources and headlines
            st.markdown("### 📰 Headlines Analyzed (by source)")
            tabs = st.tabs(list(source_map.keys()))
            for idx, (src, headlines) in enumerate(source_map.items()):
                with tabs[idx]:
                    if headlines:
                        for h in headlines[:30]:
                            st.markdown(f"- {h}")
                    else:
                        st.caption("No headlines found.")
        else:
            st.warning("Couldn’t fetch enough headlines across sources. Try again later.")

st.markdown("---")
st.caption("Built with ❤️ and vibes from GoMyCode Hackerspace")
