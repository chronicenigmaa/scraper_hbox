"""
app.py — App Buyer Lead Gen Dashboard
Run: streamlit run app.py
"""

import io, os, urllib.parse, datetime, html
import pandas as pd
from dotenv import load_dotenv
load_dotenv()
import streamlit as st
from scraper import scrape_all, score_all, QUERIES

st.set_page_config(
    page_title="Lead Gen Dashboard",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
html, body, [class*="css"] { font-family: 'Inter', 'Segoe UI', sans-serif; }
.stApp { background: #f1f5f9; }

section[data-testid="stSidebar"] {
    background: #ffffff;
    border-right: 1px solid #e2e8f0;
}
section[data-testid="stSidebar"] .block-container { padding: 0 1.2rem 2rem; }

/* Run button */
div[data-testid="stButton"] > button {
    background: #2563eb;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    font-weight: 600;
    font-size: 14px;
    width: 100%;
    padding: 0.6rem 1.2rem;
    margin-top: 4px;
}
div[data-testid="stButton"] > button:hover { background: #1d4ed8; }

/* Download button */
div[data-testid="stDownloadButton"] > button {
    background: #fff;
    color: #374151;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 500;
    padding: 0.4rem 1rem;
    width: 100%;
}

/* Metrics */
div[data-testid="metric-container"] {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 14px 18px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
}
div[data-testid="metric-container"] label {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: #94a3b8;
}
div[data-testid="metric-container"] [data-testid="stMetricValue"] {
    font-size: 26px;
    font-weight: 700;
    color: #1e293b;
}

/* Progress */
.stProgress > div > div > div { background: #2563eb !important; }

/* Hide chrome */
#MainMenu, footer { visibility: hidden; }
header[data-testid="stHeader"] { background: transparent; }
</style>
""", unsafe_allow_html=True)


# ── State ─────────────────────────────────────────────────────────
for k, v in [("leads", []), ("last_run", None), ("error", None)]:
    if k not in st.session_state:
        st.session_state[k] = v


# ── Sidebar ───────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### Lead Gen")
    st.caption("Finds people with app ideas looking for a team to build it.")
    st.divider()

    st.markdown("**Platforms**")
    use_linkedin = st.checkbox("LinkedIn",  value=True)
    use_reddit   = st.checkbox("Reddit",    value=True)
    use_twitter  = st.checkbox("Twitter/X", value=True)

    st.divider()
    st.markdown("**Custom Keywords** *(optional)*")
    st.caption("Add your own search terms, one per line.")
    custom_kw_text = st.text_area(
        label="custom_keywords",
        label_visibility="collapsed",
        placeholder="e.g. need app for my restaurant\nwant to build a booking app",
        height=100,
    )
    custom_keywords = [
        k.strip() for k in custom_kw_text.splitlines() if k.strip()
    ]

    st.divider()
    st.markdown("**Filters**")
    min_score   = st.slider("Minimum score", 1, 10, 5)
    tier_filter = st.multiselect(
        "Tier",
        options=["HOT", "WARM", "COLD"],
        default=["HOT", "WARM"],
    )

    st.divider()
    if st.session_state.last_run:
        st.caption(f"Last run: {st.session_state.last_run}")


# ── Header ────────────────────────────────────────────────────────
col_title, col_run = st.columns([5, 1])
with col_title:
    st.markdown("## App Buyer Lead Gen")
    st.caption("Surfaces people across LinkedIn, Reddit and Twitter who have an app idea and need someone to build it.")
with col_run:
    st.markdown("<div style='padding-top:8px;'>", unsafe_allow_html=True)
    run_clicked = st.button("Run Scraper", key="run_main", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)
st.divider()


# ── Run logic ─────────────────────────────────────────────────────
if run_clicked:
    st.session_state.error = None
    st.session_state.leads = []

    serpapi_key = os.getenv("SERPAPI_KEY", "")
    openai_key  = os.getenv("OPENAI_API_KEY", "")

    selected = [p for p, on in [
        ("LinkedIn",  use_linkedin),
        ("Reddit",    use_reddit),
        ("Twitter/X", use_twitter),
    ] if on]

    if not serpapi_key:
        st.session_state.error = "SERPAPI_KEY not set — add it to your .env file."
    elif not openai_key:
        st.session_state.error = "OPENAI_API_KEY not set — add it to your .env file."
    elif not selected:
        st.session_state.error = "Select at least one platform."
    else:
        total_q = sum(len(QUERIES[p]) for p in selected)
        # Each custom keyword adds 1 search per selected platform
        total_q += len(custom_keywords) * len(selected)
        done_q  = [0]
        bar     = st.progress(0, text="Starting...")

        def on_query(source, q_done, q_total):
            done_q[0] += 1
            bar.progress(
                min(done_q[0] / total_q * 0.5, 0.5),
                text=f"Scraping {source} — {q_done + 1} of {q_total}",
            )

        def on_score(s_done, s_total, name):
            bar.progress(
                0.5 + min((s_done + 1) / max(s_total, 1) * 0.5, 0.5),
                text=f"Scoring {s_done + 1} of {s_total}",
            )

        try:
            raw = scrape_all(
                serpapi_key, selected,
                on_query=on_query,
                custom_keywords=custom_keywords,
            )
            if not raw:
                st.session_state.error = "No posts found. Check your SerpApi key or try different platforms."
            else:
                leads = score_all(raw, openai_key, on_score=on_score)
                st.session_state.leads    = leads
                st.session_state.last_run = datetime.datetime.now().strftime("%d %b %Y, %H:%M")
        except RuntimeError as e:
            st.session_state.error = (
                "SerpApi monthly quota reached (100 searches/month)."
                if "QUOTA" in str(e) else str(e)
            )
        except Exception as e:
            st.session_state.error = f"Error: {e}"
        finally:
            bar.empty()


# ── Error ─────────────────────────────────────────────────────────
if st.session_state.error:
    st.warning(st.session_state.error)


# ── Filter ────────────────────────────────────────────────────────
all_leads    = st.session_state.leads
active_tiers = tier_filter or ["HOT", "WARM", "COLD"]
filtered     = [
    l for l in all_leads
    if l["score"] >= min_score and l["tier"] in active_tiers
]


# ── Metrics ───────────────────────────────────────────────────────
if all_leads:
    hot  = sum(1 for l in filtered if l["tier"] == "HOT")
    warm = sum(1 for l in filtered if l["tier"] == "WARM")
    cold = sum(1 for l in filtered if l["tier"] == "COLD")
    li   = sum(1 for l in filtered if l["source"] == "LinkedIn")
    rd   = sum(1 for l in filtered if l["source"] == "Reddit")
    tw   = sum(1 for l in filtered if l["source"] == "Twitter/X")

    c1,c2,c3,c4,c5,c6 = st.columns(6)
    c1.metric("Hot",       hot)
    c2.metric("Warm",      warm)
    c3.metric("Cold",      cold)
    c4.metric("LinkedIn",  li)
    c5.metric("Reddit",    rd)
    c6.metric("Twitter/X", tw)

    st.divider()

    row1, row2 = st.columns([5, 1])
    with row1:
        st.caption(f"{len(filtered)} leads shown")
    with row2:
        df  = pd.DataFrame(filtered)
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        st.download_button(
            "Export CSV",
            data=buf.getvalue(),
            file_name=f"leads_{st.session_state.last_run or 'export'}.csv",
            mime="text/csv",
        )


# ── Empty state ───────────────────────────────────────────────────
if not all_leads:
    st.markdown(
        "<div style='text-align:center;padding:80px 0;'>"
        "<p style='font-size:17px;font-weight:600;color:#64748b;margin-bottom:6px;'>No leads yet</p>"
        "<p style='font-size:13px;color:#94a3b8;'>Select platforms and click Run Scraper to get started.</p>"
        "</div>",
        unsafe_allow_html=True,
    )

elif not filtered:
    st.info("No leads match your current filters. Try lowering the minimum score or expanding the tier selection.")

else:
    # ── Card helpers ──────────────────────────────────────────────
    TIER_STYLES = {
        "HOT":  ("3px solid #dc2626", "#fef2f2", "#dc2626", "#fecaca"),
        "WARM": ("3px solid #d97706", "#fffbeb", "#d97706", "#fde68a"),
        "COLD": ("3px solid #2563eb", "#eff6ff", "#2563eb", "#bfdbfe"),
    }

    def make_badge(label, color, bg, border):
        s = (f"display:inline-block;font-size:10px;font-weight:700;"
             f"letter-spacing:0.5px;text-transform:uppercase;"
             f"padding:3px 9px;border-radius:4px;"
             f"color:{color};background:{bg};border:1px solid {border};margin:2px 2px 2px 0;")
        return f"<span style='{s}'>{html.escape(str(label))}</span>"

    for lead in filtered:
        tier    = lead.get("tier",      "COLD")
        source  = lead.get("source",    "")
        name    = lead.get("name",      "") or ""
        score   = lead.get("score",     0)
        intent  = lead.get("intent",    "").capitalize()
        urgency = lead.get("urgency",   "").capitalize()
        reason  = lead.get("reason",    "")
        text    = lead.get("post_text", "")
        url     = lead.get("post_url",  "")
        date    = lead.get("post_date", "")

        try:
            short_url = urllib.parse.urlparse(url).netloc.replace("www.", "") or "Open"
        except Exception:
            short_url = "Open"

        border_style, tier_bg, tier_fg, tier_border = TIER_STYLES.get(
            tier, ("3px solid #94a3b8","#f8fafc","#64748b","#e2e8f0")
        )

        # Badges
        badges = (
            make_badge(tier,      tier_fg,  tier_bg,  tier_border)
            + make_badge(f"{score}/10", "#7c3aed", "#faf5ff", "#e9d5ff")
            + make_badge(source,  "#64748b", "#f8fafc", "#e2e8f0")
            + (make_badge(intent,  "#0369a1", "#f0f9ff", "#bae6fd") if intent  else "")
            + (make_badge(urgency, "#15803d", "#f0fdf4", "#bbf7d0") if urgency else "")
        )

        name_safe   = html.escape(name)
        text_safe   = html.escape(text)
        reason_safe = html.escape(reason)
        date_safe   = html.escape(date)
        url_safe    = html.escape(url)
        short_safe  = html.escape(short_url)

        name_html = (
            f"<div style='font-size:15px;font-weight:600;color:#1e293b;"
            f"margin-bottom:10px;'>{name_safe}</div>"
        ) if name_safe else ""

        reason_html = (
            f"<div style='font-size:12px;color:#94a3b8;font-style:italic;"
            f"margin-top:10px;'>{reason_safe}</div>"
        ) if reason_safe else ""

        footer_parts = []
        if date_safe:
            footer_parts.append(
                f"<span style='font-size:11px;color:#94a3b8;'>{date_safe}</span>"
            )
        if url_safe:
            footer_parts.append(
                f"<a href='{url_safe}' target='_blank' style='"
                f"display:inline-block;padding:6px 14px;"
                f"background:#2563eb;color:#ffffff;font-size:12px;"
                f"font-weight:600;border-radius:6px;text-decoration:none;"
                f"letter-spacing:0.2px;margin-left:auto;'>"
                f"View Post &rarr;</a>"
            )
        footer_html = (
            f"<div style='display:flex;justify-content:space-between;"
            f"align-items:center;margin-top:12px;padding-top:10px;"
            f"border-top:1px solid #f1f5f9;'>"
            + "".join(footer_parts)
            + "</div>"
        ) if footer_parts else ""

        card_html = f"""
<div style="
    background:#ffffff;
    border:1px solid #e2e8f0;
    border-left:{border_style};
    border-radius:10px;
    padding:20px 24px;
    margin-bottom:14px;
    box-shadow:0 1px 3px rgba(0,0,0,0.05);
">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px;">
        {name_html if name_html else "<div></div>"}
        <div style="text-align:right;flex-shrink:0;margin-left:12px;">{badges}</div>
    </div>
    <div style="
        font-size:14.5px;color:#1e293b;line-height:1.7;
        background:#f8fafc;border:1px solid #e8edf2;
        border-radius:6px;padding:14px 16px;margin:4px 0 0;
    ">{text_safe}</div>
    {reason_html}
    {footer_html}
</div>
"""
        st.markdown(card_html, unsafe_allow_html=True)