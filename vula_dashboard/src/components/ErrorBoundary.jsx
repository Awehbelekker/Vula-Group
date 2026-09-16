/**
 * ErrorBoundary.jsx — last-resort catch for an uncaught render error.
 *
 * 2026-09-16: a missing `useState` declaration (masterSubTab) crashed the Master admin panel
 * on every click for two months, with nothing but a blank white screen — the console error was
 * the only trace, and nobody thought to check it because "the link goes nowhere" doesn't look
 * like a bug you'd open DevTools for. This turns that same class of failure into a visible,
 * actionable message instead of a silent dead end.
 */
import { Component } from "react";

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error("ErrorBoundary caught a render error:", error, info?.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div style={{
        minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
        padding: 24, fontFamily: "system-ui", background: "#F7F4EE", color: "#1E1E1E",
      }}>
        <div style={{
          maxWidth: 480, background: "#FFFFFF", border: "1px solid #DDD8CE", borderRadius: 12,
          padding: 24, boxShadow: "0 4px 16px rgba(30,30,30,0.08)",
        }}>
          <h2 style={{ margin: "0 0 8px", fontSize: 18 }}>Something broke</h2>
          <p style={{ margin: "0 0 12px", fontSize: 13.5, color: "#2A2A2A" }}>
            This screen hit an unexpected error and can't render. Reloading usually fixes it —
            if it keeps happening, tell Ian what you clicked right before this showed up.
          </p>
          <pre style={{
            margin: "0 0 16px", fontSize: 11.5, color: "#A23B2D", background: "#F0EDE5",
            padding: 10, borderRadius: 8, overflowX: "auto", whiteSpace: "pre-wrap",
          }}>{String(this.state.error?.message || this.state.error)}</pre>
          <button
            onClick={() => { this.setState({ error: null }); window.location.reload(); }}
            style={{
              padding: "8px 16px", border: "none", borderRadius: 6, background: "#2C5545",
              color: "#fff", fontSize: 13, fontWeight: 600, cursor: "pointer",
            }}
          >
            Reload
          </button>
        </div>
      </div>
    );
  }
}
