import streamlit as st
import asyncio
import os
import sys
from dotenv import load_dotenv
from app.agent.mcp_agent import MCPAgent

# Load environment
load_dotenv()

# --- Page Config ---
st.set_page_config(
    page_title="Claude MCP Investigator", 
    page_icon="🕵️‍♂️", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- Modern Dark Mode CSS ---
st.markdown("""
    <style>
    /* Main Background */
    .stApp {
        background-color: #0E1117;
        color: #E0E0E0;
    }

    /* Sidebar Styling */
    section[data-testid="stSidebar"] {
        background-color: #161B22 !important;
        border-right: 1px solid #30363D;
    }

    /* Modern Chat Bubble Styling */
    div[data-testid="stChatMessage"] {
        background-color: #161B22;
        border: 1px solid #30363D;
        border-radius: 12px;
        padding: 1.5rem;
        margin-bottom: 1rem;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
    }

    /* User Message subtle highlight */
    div[data-testid="stChatMessage"]:has(span[aria-label="user"]) {
        background-color: #1E252E;
        border-color: #3D444D;
    }

    /* Hide the default Streamlit footer/header for a cleaner look */
    header {visibility: hidden;}
    footer {visibility: hidden;}

    /* Modern Chat Input fixed at bottom */
    div[data-testid="stChatInput"] {
        border-radius: 15px;
        border: 1px solid #30363D;
        background-color: #161B22 !important;
    }
    
    /* Global Font Tweaks */
    html, body, [class*="css"]  {
        font-family: 'Inter', sans-serif;
    }
    </style>
""", unsafe_allow_html=True)

# Helper to bridge Streamlit (Sync) and MCP (Async)
def get_or_create_event_loop():
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop

# --- Session State ---
if "agent" not in st.session_state:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    st.session_state.agent = MCPAgent(api_key=api_key)
    st.session_state.messages = []

# --- Sidebar Content ---
with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/bot.png", width=80)
    st.title("Control Center")
    st.markdown("---")
    
    st.subheader("System Status")
    st.success("🟢 MCP Engine Active")
    st.info("🌐 Tool: DuckDuckGo Search")
    
    st.markdown("---")
    if st.button("🗑️ Clear Conversation", use_container_width=True):
        st.session_state.messages = []
        st.session_state.agent.history = []
        st.rerun()

# --- Main Interface ---
st.title("🕵️‍♂️ Claude Live Investigator")
st.markdown("*Real-time web-connected intelligence via Model Context Protocol*")

# Display Chat History
for message in st.session_state.messages:
    # Adding an avatar parameter for extra modern feel
    avatar = "👤" if message["role"] == "user" else "🤖"
    with st.chat_message(message["role"], avatar=avatar):
        st.markdown(message["content"])

# User Input
if prompt := st.chat_input("Ask me for live data, news, or stock prices..."):
    # UI: User Message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar="👤"):
        st.markdown(prompt)

    # UI: Assistant Thinking
    with st.chat_message("assistant", avatar="🤖"):
        # We use st.status for a polished "Tool Execution" view
        with st.status("🔍 Consulted DuckDuckGo MCP...", expanded=False) as status:
            loop = get_or_create_event_loop()
            try:
                response = loop.run_until_complete(st.session_state.agent.run_agent(prompt))
                status.update(label="✅ Search Complete", state="complete")
                
                st.markdown(response)
                st.session_state.messages.append({"role": "assistant", "content": response})
            except Exception as e:
                status.update(label="❌ Error occurred", state="error")
                st.error(f"Something went wrong: {str(e)}")
                if "ClosedResourceError" in str(e):
                    st.session_state.agent.session = None