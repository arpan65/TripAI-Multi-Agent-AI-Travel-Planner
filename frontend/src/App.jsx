import React, { useState } from "react";

const API_BASE = import.meta.env.VITE_API_URL || "";

function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState(null);
  const [error, setError] = useState("");

  const sendMessage = async () => {
    const trimmed = input.trim();
    if (!trimmed || loading) return;

    setError("");
    setLoading(true);
    setMessages((prev) => [...prev, { role: "user", content: trimmed }]);
    setInput("");

    try {
      const res = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: trimmed,
          session_id: sessionId
        })
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Unable to get response from backend");
      }

      const data = await res.json();
      setSessionId(data.session_id);
      setMessages((prev) => [...prev, { role: "assistant", content: data.reply }]);
    } catch (err) {
      setError(err.message || "Unexpected error occurred");
    } finally {
      setLoading(false);
    }
  };

  const clearConversation = async () => {
    setMessages([]);
    setError("");

    if (!sessionId) return;

    try {
      await fetch(`${API_BASE}/api/reset`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId })
      });
    } catch {
      // Ignore reset errors and allow local clear.
    } finally {
      setSessionId(null);
    }
  };

  const handleSubmit = (event) => {
    event.preventDefault();
    sendMessage();
  };

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="logo-wrap">
          <div className="logo">MCP</div>
          <h2>Control Center</h2>
        </div>
        <div className="status-card">
          <h3>System Status</h3>
          <p>
            <span className="dot online" />
            MCP Engine Active
          </p>
          <p>
            <span className="dot info" />
            Tool: DuckDuckGo Search
          </p>
        </div>
        <button className="clear-btn" onClick={clearConversation}>
          Clear Conversation
        </button>
      </aside>

      <main className="chat-shell">
        <header className="topbar">
          <h1>Claude Live Investigator</h1>
          <p>Real-time web-connected intelligence via Model Context Protocol</p>
        </header>

        <section className="chat-list">
          {messages.length === 0 ? (
            <div className="empty-state">
              Ask for live data, news, stock prices, or browser-assisted tasks.
            </div>
          ) : (
            messages.map((message, idx) => (
              <article key={`${message.role}-${idx}`} className={`bubble ${message.role}`}>
                <div className="bubble-role">{message.role === "user" ? "You" : "Assistant"}</div>
                <div className="bubble-content">{message.content}</div>
              </article>
            ))
          )}
        </section>

        {error ? <div className="error-box">{error}</div> : null}

        <form className="input-row" onSubmit={handleSubmit}>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask me for live data, news, or stock prices..."
            disabled={loading}
          />
          <button type="submit" disabled={loading || !input.trim()}>
            {loading ? "Thinking..." : "Send"}
          </button>
        </form>
      </main>
    </div>
  );
}

export default App;
