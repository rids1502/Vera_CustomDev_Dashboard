import streamlit as st
import requests
import csv
import io
import re
import html
import os
import json
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY")
GEMINI_URL     = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

SHEET_ID      = "1aume7ELdKEZQFZE_KcwBzO2L7PuzD1lDVvO1fUNB1cg"
SHEET_GID     = "341805322"
SHEET_CSV_URL = (
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
    f"/export?format=csv&gid={SHEET_GID}"
)

# Column indices — update here if sheet columns change
COL_FEED_ID      = 0
COL_PARENT_ID    = 1
COL_PARENT_NAME  = 2
COL_STATUS       = 3
COL_CREATED_DATE = 4   # Parent.CreatedDate
COL_INSERTED_BY  = 5
COL_BODY         = 6
COL_CMT_ID       = 7
COL_CMT_BODY     = 8
COL_CMT_BY       = 9
COL_CMT_DATE     = 10

REJECTED = ["Rejected", "Deferred"]
STALLED  = ["On-hold"]
STATUS_ICON = {
    "Lead": "🟡", "Rejected": "🔴", "Deferred": "⚫",
    "Awaiting SoW": "🔵", "Deployed": "🟢", "On-hold": "⏸️",
    "Development Requested": "🟣", "UAT": "🔵", "Dev Assignment": "🔵",
}

# ── Persistence — JSON file ───────────────────────────────────────────────────
PERSIST_FILE = Path("vera_action_states.json")

def load_file_states():
    """Load action states from JSON file — survives page reloads."""
    try:
        if PERSIST_FILE.exists():
            return json.loads(PERSIST_FILE.read_text())
    except:
        pass
    return {}

def save_file_states(states):
    """Write action states to JSON file."""
    try:
        PERSIST_FILE.write_text(json.dumps(states, indent=2))
    except Exception as e:
        st.warning(f"Could not save state: {e}")

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Vera · Custom Dev Intelligence",
    page_icon="🔵",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
.block-container { padding-top: 1rem; padding-bottom: 0; max-width: 100%; }
[data-testid="metric-container"] {
    background: #ffffff; border: 1px solid rgba(0,0,0,0.08);
    border-radius: 8px; padding: 12px 16px;
}
section[data-testid="column"]:first-child .stButton button {
    text-align: left; background: #ffffff;
    border: 1px solid rgba(0,0,0,0.08); border-radius: 6px;
    font-size: 12px; padding: 6px 10px; margin-bottom: 2px;
    white-space: normal; height: auto;
}
section[data-testid="column"]:first-child .stButton button:hover {
    background: #EEF1FF; border-color: #2D4EFF;
}
.month-header {
    font-size: 11px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.06em; color: #9E9E9A;
    padding: 8px 4px 4px; border-bottom: 1px solid rgba(0,0,0,0.08);
    margin-bottom: 4px;
}
.chat-user {
    background: #2D4EFF; color: white; padding: 8px 12px;
    border-radius: 12px 12px 3px 12px; margin: 4px 0 4px 20px;
    font-size: 13px; line-height: 1.5; word-wrap: break-word;
}
.chat-ai {
    background: #F7F6F3; color: #1A1A18; padding: 8px 12px;
    border-radius: 12px 12px 12px 3px; margin: 4px 20px 4px 0;
    font-size: 13px; line-height: 1.6; border: 1px solid rgba(0,0,0,0.08);
    word-wrap: break-word; white-space: pre-wrap;
}
.chat-label { font-size: 10px; color: #9E9E9A; margin-bottom: 2px; }
</style>
""", unsafe_allow_html=True)

# ── Helpers ───────────────────────────────────────────────────────────────────
def clean_text(text):
    if not text: return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"@[\w][\w\s]*?\u200b\xa0?", "", text)
    text = re.sub(r"\xa0", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def extract_name(raw):
    m = re.search(r"Name=([^,}]+)", raw)
    return m.group(1).strip() if m else raw.split(",")[0].strip("{} ")

def parse_date(date_str):
    if not date_str or len(date_str) < 10:
        return "", None, "Unknown"
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return date_str[:10], dt, dt.strftime("%B %Y")
    except:
        return "", None, "Unknown"

# ── Load sheet ────────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_deliverables():
    try:
        resp = requests.get(SHEET_CSV_URL, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        st.error(f"Could not fetch sheet: {e}")
        return []

    reader = csv.reader(io.StringIO(resp.text))
    next(reader)
    deliverables = {}

    for row in reader:
        if len(row) <= COL_BODY: continue

        feed_id      = row[COL_FEED_ID].strip()
        parent_id    = row[COL_PARENT_ID].strip()
        parent_name  = row[COL_PARENT_NAME].strip()
        status       = row[COL_STATUS].strip()
        created_raw  = row[COL_CREATED_DATE].strip()
        inserted_by  = row[COL_INSERTED_BY].strip()
        body         = clean_text(row[COL_BODY])
        comment_body = clean_text(row[COL_CMT_BODY]) if len(row) > COL_CMT_BODY else ""
        comment_by   = extract_name(row[COL_CMT_BY]) if len(row) > COL_CMT_BY else ""
        comment_date = row[COL_CMT_DATE][:10].strip() if len(row) > COL_CMT_DATE else ""

        if not parent_name: continue

        if parent_name not in deliverables:
            created_short, created_dt, month_label = parse_date(created_raw)
            deliverables[parent_name] = {
                "name": parent_name, "status": status,
                "parent_id": parent_id,
                "created_date": created_short,
                "created_dt": created_dt,
                "month_label": month_label,
                "posts": {}, "comments": []
            }

        if feed_id and body and feed_id not in deliverables[parent_name]["posts"]:
            deliverables[parent_name]["posts"][feed_id] = {
                "author": inserted_by, "body": body
            }

        if comment_body:
            deliverables[parent_name]["comments"].append({
                "author": comment_by, "body": comment_body, "date": comment_date
            })

    result = []
    for d in deliverables.values():
        posts     = list(d["posts"].values())
        posts_txt = " | ".join(f"{p['author']}: {p['body']}" for p in posts)
        cmts      = d["comments"][-10:]
        cmts_txt  = " | ".join(f"{c['author']} ({c['date']}): {c['body']}" for c in cmts)
        thread    = f"{posts_txt} REPLIES: {cmts_txt}" if cmts_txt else posts_txt

        result.append({
            "name":          d["name"],
            "status":        d["status"],
            "parent_id":     d["parent_id"],
            "created_date":  d["created_date"],
            "created_dt":    d["created_dt"],
            "month_label":   d["month_label"],
            "post_count":    len(posts),
            "comment_count": len(d["comments"]),
            "thread":        thread,
            "raw_posts":     posts,
            "raw_comments":  d["comments"],
        })

    result.sort(key=lambda x: x["created_date"] or "0000-00-00", reverse=True)
    return result

# ── Gemini ────────────────────────────────────────────────────────────────────
def call_gemini(prompt, system=""):
    full = f"{system}\n\n{prompt}" if system else prompt
    resp = requests.post(
        GEMINI_URL,
        headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY},
        json={"contents": [{"role": "user", "parts": [{"text": full}]}],
              "generationConfig": {"maxOutputTokens": 1000, "temperature": 0.2}},
        timeout=30,
    )
    data = resp.json()
    if not resp.ok:
        raise Exception(data.get("error", {}).get("message", "Gemini error"))
    return data["candidates"][0]["content"]["parts"][0]["text"]

def generate_summary(d):
    stall = (
        '"stall_reason":"one sentence what is blocking",'
        '"expected_pickup":"date or Not confirmed",'
        '"last_activity":"most recent poster and date",'
        if d["status"] in STALLED else ""
    )
    prompt = f"""You are a PM assistant at Vera Solutions. Analyse this Chatter thread.
Respond ONLY with valid JSON — no markdown, no explanation, no text outside the JSON:
{{"summary":"one sentence max 25 words",{stall}"actions":["action with owner name","action 2"]}}
Rules:
- summary: max 25 words, present tense
- actions: 2-4 items, each with a specific owner name
- Return ONLY the JSON object. Nothing else.

Deliverable: "{d['name']}"
Status: {d['status']}
Created: {d['created_date']}
Thread: {d['thread']}"""
    raw = call_gemini(prompt).strip()
    # Strip any markdown fences if present
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"^```\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw).strip()
    return json.loads(raw)

# ── Session state init ────────────────────────────────────────────────────────
if "ai_cache"      not in st.session_state: st.session_state.ai_cache      = {}
if "chat_history"  not in st.session_state: st.session_state.chat_history  = []
if "selected_idx"  not in st.session_state: st.session_state.selected_idx  = None

# Load action states from file on every page load
if "action_states" not in st.session_state:
    st.session_state.action_states = load_file_states()

DELIVERABLES = load_deliverables()

# ── Nav ───────────────────────────────────────────────────────────────────────
nav_l, nav_m, nav_r = st.columns([3, 3, 1])
with nav_l:
    st.markdown("### 🔵 Vera · Custom Dev Intelligence")
with nav_m:
    st.caption(f"{len(DELIVERABLES)} deliverables · live from Salesforce · Gemini 2.5 Flash")
with nav_r:
    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.session_state.ai_cache = {}
        st.rerun()

st.divider()

# ── KPIs ──────────────────────────────────────────────────────────────────────
total    = len(DELIVERABLES)
active   = sum(1 for d in DELIVERABLES if d["status"] in ["Development Requested","Dev Assignment","UAT"])
awaiting = sum(1 for d in DELIVERABLES if d["status"] in ["Awaiting SoW","Lead"])
blocked  = sum(1 for d in DELIVERABLES if d["status"] in ["On-hold","Rejected","Deferred"])
deployed = sum(1 for d in DELIVERABLES if d["status"] == "Deployed")

for col, label, val in zip(st.columns(5),
    ["Total","In development","Awaiting approval","Blocked / stalled","Deployed"],
    [total, active, awaiting, blocked, deployed]):
    col.metric(label, val)

st.divider()

# ── 3-column layout ───────────────────────────────────────────────────────────
col_list, col_detail, col_chat = st.columns([1.2, 2.2, 1.4])

# ═══════════════════════════════
# LEFT — list grouped by created month
# ═══════════════════════════════
with col_list:
    st.markdown("**Deliverables**")
    search = st.text_input("", placeholder="🔍 Search...", label_visibility="collapsed")

    filtered = [d for d in DELIVERABLES if not search
                or search.lower() in d["name"].lower()
                or search.lower() in d["status"].lower()
                or search.lower() in d["month_label"].lower()]

    groups = {}
    for d in filtered:
        groups.setdefault(d["month_label"], {"dt": d["created_dt"], "items": []})
        groups[d["month_label"]]["items"].append(d)

    sorted_months = sorted(
        groups.keys(),
        key=lambda m: groups[m]["dt"] or datetime(1900,1,1),
        reverse=True
    )

    for month in sorted_months:
        st.markdown(f"<div class='month-header'>📅 {month}</div>", unsafe_allow_html=True)
        for d in groups[month]["items"]:
            idx  = DELIVERABLES.index(d)
            acts = st.session_state.action_states.get(str(idx), [])
            done = sum(1 for a in acts if a.get("confirmed"))
            prog = f" ✅{done}/{len(acts)}" if acts else ""
            icon = STATUS_ICON.get(d["status"], "⚪")
            name_short = d["name"][:36] + "…" if len(d["name"]) > 36 else d["name"]
            if st.button(f"{icon} {name_short}{prog}", key=f"btn_{idx}", use_container_width=True):
                st.session_state.selected_idx = idx

# ═══════════════════════════════
# MIDDLE — detail
# ═══════════════════════════════
with col_detail:
    idx = st.session_state.selected_idx

    if idx is None:
        st.info("← Select a deliverable to see AI summary and action items.")
    else:
        d           = DELIVERABLES[idx]
        is_rejected = d["status"] in REJECTED
        is_stalled  = d["status"] in STALLED
        idx_key     = str(idx)

        # Deliverable header
        st.markdown(f"**{d['name']}**")
        created_fmt = (
            datetime.strptime(d["created_date"], "%Y-%m-%d").strftime("%d %b %Y")
            if d["created_date"] else "—"
        )
        st.markdown(
            f"{STATUS_ICON.get(d['status'],'⚪')} **{d['status']}** "
            f"&nbsp;·&nbsp; 📅 Created {created_fmt} "
            f"&nbsp;·&nbsp; {d['post_count']} posts "
            f"&nbsp;·&nbsp; {d['comment_count']} replies"
        )
        st.markdown("")

        # Only generate AI if:
        # 1. Not rejected
        # 2. Not already in ai_cache this session
        # 3. Not already saved in file (action states exist from previous session)
        already_saved = idx_key in st.session_state.action_states
        if not is_rejected and idx not in st.session_state.ai_cache and not already_saved:
            with st.spinner("Analysing thread with Gemini..."):
                try:
                    result = generate_summary(d)
                    st.session_state.ai_cache[idx] = result
                    st.session_state.action_states[idx_key] = [
                        {"text": a, "confirmed": False, "editing": False}
                        for a in result.get("actions", [])
                    ]
                    save_file_states(st.session_state.action_states)
                except Exception as e:
                    st.error(f"Gemini error: {e}")
        elif not is_rejected and idx not in st.session_state.ai_cache and already_saved:
            # Regenerate summary only (actions already saved, don't overwrite)
            with st.spinner("Regenerating summary..."):
                try:
                    result = generate_summary(d)
                    st.session_state.ai_cache[idx] = result
                except Exception as e:
                    st.error(f"Gemini error: {e}")

        ai = st.session_state.ai_cache.get(idx)

        # Summary card
        with st.container(border=True):
            st.markdown("**🤖 AI Summary**")
            if is_rejected:
                st.warning(f"No summary — deliverable is **{d['status'].lower()}** and no longer active.")
            elif ai:
                st.write(ai.get("summary", ""))
            else:
                st.caption("Summary will appear here.")

        # Stall card
        if is_stalled and ai:
            with st.container(border=True):
                st.markdown("**⏸ Stalled Details**")
                s1, s2 = st.columns(2)
                with s1:
                    st.markdown("**Stall reason**"); st.write(ai.get("stall_reason","—"))
                    st.markdown("**Expected pickup**"); st.write(ai.get("expected_pickup","Not confirmed"))
                with s2:
                    st.markdown("**Last activity**"); st.write(ai.get("last_activity","—"))

        # Action items
        with st.container(border=True):
            st.markdown("**✅ Key Action Items**")

            if is_rejected:
                st.error("🚫 No action items — deliverable is rejected/deferred. No further action required unless reopened.")

            elif idx_key in st.session_state.action_states:
                acts    = st.session_state.action_states[idx_key]
                changed = False

                for i, action in enumerate(acts):
                    ct, cb1, cb2, cb3 = st.columns([3.5, 1, 1, 0.6])

                    with ct:
                        if action.get("editing"):
                            new_val = st.text_input("", value=action["text"],
                                key=f"inp_{idx}_{i}", label_visibility="collapsed")
                        elif action.get("confirmed"):
                            st.success(f"✓ {action['text']}")
                        else:
                            st.write(f"{i+1}. {action['text']}")

                    with cb1:
                        if action.get("editing"):
                            if st.button("Save", key=f"save_{idx}_{i}"):
                                acts[i]["text"]    = new_val
                                acts[i]["editing"] = False
                                changed = True
                        elif action.get("confirmed"):
                            if st.button("Undo", key=f"undo_{idx}_{i}"):
                                acts[i]["confirmed"] = False
                                changed = True
                        else:
                            if st.button("✓ Confirm", key=f"conf_{idx}_{i}"):
                                acts[i]["confirmed"] = True
                                changed = True

                    with cb2:
                        if action.get("editing"):
                            if st.button("Cancel", key=f"cncl_{idx}_{i}"):
                                acts[i]["editing"] = False
                                changed = True
                        elif not action.get("confirmed"):
                            if st.button("Edit", key=f"edit_{idx}_{i}"):
                                acts[i]["editing"] = True
                                changed = True

                    with cb3:
                        if not action.get("editing"):
                            if st.button("🗑", key=f"del_{idx}_{i}", help="Delete"):
                                acts.pop(i)
                                changed = True

                    if changed:
                        st.session_state.action_states[idx_key] = acts
                        save_file_states(st.session_state.action_states)
                        st.rerun()

            elif not is_rejected and ai:
                # Fallback — populate from AI result
                st.session_state.action_states[idx_key] = [
                    {"text": a, "confirmed": False, "editing": False}
                    for a in ai.get("actions", [])
                ]
                save_file_states(st.session_state.action_states)
                st.rerun()

        # Chatter thread
        with st.expander(f"💬 Chatter thread ({d['post_count']+d['comment_count']} messages)"):
            for p in d.get("raw_posts", []):
                st.markdown(f"**{p['author']}** *(original post)*")
                st.write(p["body"])
                st.divider()
            for c in d.get("raw_comments", []):
                st.markdown(
                    f"&nbsp;&nbsp;↳ **{c['author']}** "
                    f"<span style='color:grey;font-size:12px'>{c['date']}</span>",
                    unsafe_allow_html=True)
                st.write(c["body"])

# ═══════════════════════════════
# RIGHT — chat
# ═══════════════════════════════
with col_chat:
    st.markdown("**💬 Ask Claude**")
    st.caption("Ask about any deliverable or the pipeline")
    st.markdown("---")

    all_ctx = "\n\n---\n\n".join(
        f'"{d["name"]}" | {d["status"]} | Created: {d["created_date"]}\n{d["thread"]}'
        for d in DELIVERABLES
    )
    sys_prompt = (
        "You are a PM assistant for Riddhi Bhogaonkar at Vera Solutions. "
        "Answer clearly in 3-5 sentences. Use plain language. "
        "Always name the specific deliverable and owner when relevant.\n\n"
        f"PIPELINE CONTEXT:\n{all_ctx}"
    )

    # Chat history display
    chat_container = st.container(height=400)
    with chat_container:
        if not st.session_state.chat_history:
            st.markdown(
                "<div class='chat-ai'>Hi! I have full context on all 13 deliverables. "
                "Ask me what's blocked, who needs follow-up, or get a leadership summary.</div>",
                unsafe_allow_html=True)

        for msg in st.session_state.chat_history:
            if msg["role"] == "user":
                st.markdown(
                    f"<div class='chat-label'>You</div>"
                    f"<div class='chat-user'>{msg['content']}</div>",
                    unsafe_allow_html=True)
            else:
                # Use st.write inside expander for long responses
                st.markdown("<div class='chat-label'>Gemini</div>", unsafe_allow_html=True)
                st.info(msg["content"])

    # Suggestion chips
    st.markdown("")
    for q in ["What's blocked?", "Who needs follow-up?", "Leadership summary"]:
        if st.button(q, key=f"chip_{q}", use_container_width=True):
            st.session_state.chat_history.append({"role": "user", "content": q})
            st.rerun()

    # Process last user message
    if st.session_state.chat_history and st.session_state.chat_history[-1]["role"] == "user":
        last_q = st.session_state.chat_history[-1]["content"]
        with st.spinner("Thinking..."):
            try:
                reply = call_gemini(last_q, system=sys_prompt)
                st.session_state.chat_history.append({"role": "assistant", "content": reply})
                st.rerun()
            except Exception as e:
                err = f"Error: {e}"
                st.session_state.chat_history.append({"role": "assistant", "content": err})
                st.rerun()

    # Chat input
    if user_input := st.chat_input("Ask about your deliverables..."):
        st.session_state.chat_history.append({"role": "user", "content": user_input})
        st.rerun()