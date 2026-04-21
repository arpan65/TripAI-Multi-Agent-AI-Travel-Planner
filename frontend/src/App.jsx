import React, { useEffect, useRef, useState } from "react";

const API_BASE = import.meta.env.VITE_API_URL || "";

const toSafeString = (value) => {
  if (typeof value === "string") return value;
  if (value == null) return "";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
};

function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState(null);
  const [error, setError] = useState("");
  const chatListRef = useRef(null);

  useEffect(() => {
    const container = chatListRef.current;
    if (!container) return;
    container.scrollTop = container.scrollHeight;
  }, [messages, loading, error]);

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
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: toSafeString(data.reply) || "No response from backend." }
      ]);
    } catch (err) {
      setError(toSafeString(err?.message ?? err) || "Unexpected error occurred");
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

        <section className="chat-list" ref={chatListRef}>
          {messages.length === 0 && !loading ? (
            <div className="empty-state">
              Ask for live data, news, stock prices, or browser-assisted tasks.
            </div>
          ) : (
            <>
              {messages.map((message, idx) => (
                <article
                  key={`${message?.role === "assistant" ? "assistant" : "user"}-${idx}`}
                  className={`bubble-row ${message?.role === "assistant" ? "assistant" : "user"}`}
                >
                  <div className={`bubble ${message?.role === "assistant" ? "assistant" : "user"}`}>
                    <div className="bubble-role">
                      {message?.role === "assistant" ? "Assistant" : "You"}
                    </div>
                    <div className="bubble-content">
                      {toSafeString(message?.content)}
                    </div>
                  </div>
                </article>
              ))}
              {loading ? (
                <article className="bubble-row assistant">
                  <div className="bubble assistant thinking-bubble">
                    <div className="bubble-role">Assistant</div>
                    <div className="thinking-row">
                      <span>Thinking</span>
                      <span className="thinking-dots" aria-hidden="true">
                        <i />
                        <i />
                        <i />
                      </span>
                    </div>
                  </div>
                </article>
              ) : null}
            </>
          )}
        </section>

        {error ? <div className="error-box">{toSafeString(error)}</div> : null}

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
