import streamlit as st
import requests
import csv
import io
import re
import html
import os
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY")
GEMINI_URL     = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

SHEET_ID  = "1aume7ELdKEZQFZE_KcwBzO2L7PuzD1lDVvO1fUNB1cg"
SHEET_GID = "341805322"
SHEET_CSV_URL = (
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
    f"/export?format=csv&gid={SHEET_GID}"
)

REJECTED = ["Rejected", "Deferred"]
STALLED  = ["On-hold"]
STATUS_ICON = {
    "Lead": "🟡", "Rejected": "🔴", "Deferred": "⚫",
    "Awaiting SoW": "🔵", "Deployed": "🟢", "On-hold": "⏸️",
    "Development Requested": "🟣", "UAT": "🔵", "Dev Assignment": "🔵",
}

st.set_page_config(page_title="Vera · Custom Dev Intelligence", page_icon="🔵", layout="wide")

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

@st.cache_data(ttl=300)
def load_deliverables():
    try:
        resp = requests.get(SHEET_CSV_URL, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        st.error(f"Could not fetch sheet: {e}")
        return []

    reader  = csv.reader(io.StringIO(resp.text))
    next(reader)  # skip header
    deliverables = {}

    for row in reader:
        if len(row) < 6: continue
        feed_id      = row[0].strip()
        parent_id    = row[1].strip()
        parent_name  = row[2].strip()
        status       = row[3].strip()
        inserted_by  = row[4].strip()
        body         = clean_text(row[5])
        comment_body = clean_text(row[7]) if len(row) > 7 else ""
        comment_by   = extract_name(row[8]) if len(row) > 8 else ""
        comment_date = row[9][:10].strip() if len(row) > 9 else ""

        if not parent_name: continue
        if parent_name not in deliverables:
            deliverables[parent_name] = {
                "name": parent_name, "status": status,
                "parent_id": parent_id, "posts": {}, "comments": []
            }
        if feed_id and body and feed_id not in deliverables[parent_name]["posts"]:
            deliverables[parent_name]["posts"][feed_id] = {"author": inserted_by, "body": body}
        if comment_body:
            deliverables[parent_name]["comments"].append(
                {"author": comment_by, "body": comment_body, "date": comment_date}
            )

    result = []
    for d in deliverables.values():
        posts      = list(d["posts"].values())
        posts_txt  = " | ".join(f"{p['author']}: {p['body']}" for p in posts)
        cmts       = d["comments"][-10:]
        cmts_txt   = " | ".join(f"{c['author']} ({c['date']}): {c['body']}" for c in cmts)
        thread     = f"{posts_txt} REPLIES: {cmts_txt}" if cmts_txt else posts_txt
        result.append({
            "name": d["name"], "status": d["status"], "parent_id": d["parent_id"],
            "post_count": len(posts), "comment_count": len(d["comments"]),
            "thread": thread, "raw_posts": posts, "raw_comments": d["comments"]
        })
    return result

def call_gemini(prompt, system=""):
    full = f"{system}\n\n{prompt}" if system else prompt
    resp = requests.post(
        GEMINI_URL,
        headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY},
        json={"contents": [{"role": "user", "parts": [{"text": full}]}],
              "generationConfig": {
                  "maxOutputTokens": 2000,
                  "temperature": 0.2,
                  "thinkingConfig": {"thinkingBudget": 0},
              }},
        timeout=30,
    )
    try:
        data = resp.json()
    except Exception:
        raise Exception(f"Gemini API returned an unexpected response (HTTP {resp.status_code}). Check your API key.")
    if not resp.ok:
        raise Exception(data.get("error", {}).get("message", "Gemini API error"))
    return data["candidates"][0]["content"]["parts"][0]["text"]

def generate_summary(d):
    import json as _json
    stall = (
        '"stall_reason":"one sentence what is blocking",'
        '"expected_pickup":"date or Not confirmed",'
        '"last_activity":"most recent poster and date",'
        if d["status"] in STALLED else ""
    )
    prompt = f"""You are a PM assistant at Vera Solutions. Analyse this Chatter thread.
Respond ONLY with valid JSON, no markdown fences:
{{"summary":"one sentence max 25 words",{stall}"actions":["action with owner name","action 2"]}}
Rules: summary max 25 words, 2-4 action items with owner names, ONLY JSON no extra text.
Deliverable: "{d['name']}"
Status: {d['status']}
Thread: {d['thread']}"""
    import re as _re
    raw = call_gemini(prompt).strip()
    raw = _re.sub(r"```json\s*", "", raw)
    raw = _re.sub(r"```\s*", "", raw)
    raw = raw.strip()
    match = _re.search(r"\{.*\}", raw, _re.DOTALL)
    if match:
        raw = match.group()
    return _json.loads(raw)

# Session state
for k, v in [("ai_cache",{}),("action_states",{}),("chat_history",[]),("selected_idx",None)]:
    if k not in st.session_state: st.session_state[k] = v

DELIVERABLES = load_deliverables()

# Header
col_h, col_r = st.columns([6, 1])
with col_h:
    st.markdown("## 🔵 Vera · Custom Dev Intelligence")
    st.caption(f"{len(DELIVERABLES)} deliverables · live from Salesforce Chatter · powered by Gemini 2.5 Flash")
with col_r:
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.session_state.ai_cache = {}
        st.session_state.action_states = {}
        st.rerun()

st.divider()

# KPIs
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

left, right = st.columns([1, 2])

# Sidebar list
with left:
    st.markdown("### Deliverables")
    search = st.text_input("Search", placeholder="Name or status...")
    filtered = [d for d in DELIVERABLES if not search
                or search.lower() in d["name"].lower()
                or search.lower() in d["status"].lower()]

    for d in filtered:
        idx  = DELIVERABLES.index(d)
        acts = st.session_state.action_states.get(idx, [])
        done = sum(1 for a in acts if a["confirmed"])
        prog = f" · ✅{done}/{len(acts)}" if acts else ""
        icon = STATUS_ICON.get(d["status"], "⚪")
        lbl  = f"{icon} {d['name'][:40]}{'…' if len(d['name'])>40 else ''}{prog}"
        if st.button(lbl, key=f"btn_{idx}", use_container_width=True):
            st.session_state.selected_idx = idx

# Detail panel
with right:
    idx = st.session_state.selected_idx
    if idx is None:
        st.info("← Select a deliverable to see AI summary and action items.")
    else:
        d           = DELIVERABLES[idx]
        is_rejected = d["status"] in REJECTED
        is_stalled  = d["status"] in STALLED
        icon        = STATUS_ICON.get(d["status"], "⚪")

        st.markdown(f"### {d['name']}")
        st.markdown(f"{icon} **{d['status']}** &nbsp;·&nbsp; {d['post_count']} posts &nbsp;·&nbsp; {d['comment_count']} replies")

        if not is_rejected and idx not in st.session_state.ai_cache:
            with st.spinner("Analysing thread with Gemini..."):
                try:
                    result = generate_summary(d)
                    st.session_state.ai_cache[idx] = result
                    st.session_state.action_states[idx] = [
                        {"text": a, "confirmed": False, "editing": False}
                        for a in result.get("actions", [])
                    ]
                except Exception as e:
                    st.error(f"Gemini error: {e}")

        ai = st.session_state.ai_cache.get(idx)

        # Summary
        with st.container(border=True):
            st.markdown("**🤖 AI Summary**")
            if is_rejected:
                st.warning(f"No summary — deliverable is **{d['status'].lower()}** and no longer active.")
            elif ai:
                st.write(ai.get("summary",""))
            else:
                st.caption("Select a deliverable to generate.")

        # Stall card
        if is_stalled and ai:
            with st.container(border=True):
                st.markdown("**⏸ Stalled Details**")
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown("**Stall reason**"); st.write(ai.get("stall_reason","—"))
                    st.markdown("**Expected pickup**"); st.write(ai.get("expected_pickup","Not confirmed"))
                with c2:
                    st.markdown("**Last activity**"); st.write(ai.get("last_activity","—"))

        # Action items
        with st.container(border=True):
            st.markdown("**✅ Key Action Items**")
            if is_rejected:
                st.error("🚫 No action items — deliverable is rejected/deferred. No further action required unless reopened.")
            elif ai:
                acts = st.session_state.action_states.get(idx, [])
                for i, action in enumerate(acts):
                    ct, cb1, cb2 = st.columns([4, 1, 1])
                    with ct:
                        if action["editing"]:
                            new_val = st.text_input("", value=action["text"],
                                                    key=f"inp_{idx}_{i}", label_visibility="collapsed")
                        elif action["confirmed"]:
                            st.success(f"✓ {action['text']}")
                        else:
                            st.write(f"{i+1}. {action['text']}")
                    with cb1:
                        if action["editing"]:
                            if st.button("Save", key=f"save_{idx}_{i}"):
                                st.session_state.action_states[idx][i]["text"] = new_val
                                st.session_state.action_states[idx][i]["editing"] = False
                                st.rerun()
                        elif action["confirmed"]:
                            if st.button("Undo", key=f"undo_{idx}_{i}"):
                                st.session_state.action_states[idx][i]["confirmed"] = False
                                st.rerun()
                        else:
                            if st.button("✓ Confirm", key=f"conf_{idx}_{i}"):
                                st.session_state.action_states[idx][i]["confirmed"] = True
                                st.rerun()
                    with cb2:
                        if action["editing"]:
                            if st.button("Cancel", key=f"cncl_{idx}_{i}"):
                                st.session_state.action_states[idx][i]["editing"] = False
                                st.rerun()
                        elif not action["confirmed"]:
                            if st.button("Edit", key=f"edit_{idx}_{i}"):
                                st.session_state.action_states[idx][i]["editing"] = True
                                st.rerun()

        # Thread
        with st.expander(f"💬 Chatter thread ({d['post_count']+d['comment_count']} messages)"):
            for p in d.get("raw_posts", []):
                st.markdown(f"**{p['author']}** *(post)*")
                st.write(p["body"])
                st.divider()
            for c in d.get("raw_comments", []):
                st.markdown(
                    f"&nbsp;&nbsp;↳ **{c['author']}** "
                    f"<span style='color:grey;font-size:12px'>{c['date']}</span>",
                    unsafe_allow_html=True)
                st.write(c["body"])

st.divider()

# Chat
st.markdown("### 💬 Ask about your pipeline")
all_ctx = "\n\n---\n\n".join(
    f'"{d["name"]}" | {d["status"]}\n{d["thread"]}' for d in DELIVERABLES)
sys_prompt = (
    "You are a PM assistant for Riddhi Bhogaonkar at Vera Solutions. "
    "Answer in 2-4 sentences, plain language. Name specific deliverables and owners.\n\n"
    f"CONTEXT:\n{all_ctx}"
)

for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

if not st.session_state.chat_history:
    st.caption("Try:")
    for col, q in zip(st.columns(3), [
        "What's blocked right now?",
        "Who needs follow-up this week?",
        "Give me a leadership summary"]):
        with col:
            if st.button(q):
                st.session_state.chat_history.append({"role":"user","content":q})
                st.rerun()

if st.session_state.chat_history and st.session_state.chat_history[-1]["role"] == "user":
    q = st.session_state.chat_history[-1]["content"]
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                reply = call_gemini(q, system=sys_prompt)
                st.write(reply)
                st.session_state.chat_history.append({"role":"assistant","content":reply})
            except Exception as e:
                st.error(str(e))

if user_input := st.chat_input("Ask about your deliverables..."):
    st.session_state.chat_history.append({"role":"user","content":user_input})
    st.rerun()
