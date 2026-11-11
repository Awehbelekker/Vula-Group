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
import { reportError } from "../lib/appErrors";

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
    reportError(error, { source: "render" });   // Master › Health sees it (lib/appErrors.js)
  }

  componentDidUpdate(prev) {
    // A screen boundary clears when the person moves to another screen.
    if (this.state.error && this.props.resetKey !== prev.resetKey) this.setState({ error: null });
  }

  render() {
    if (!this.state.error) return this.props.children;
    if (this.props.screen) {
      // One screen failed: the rest of the app (menu, other screens) keeps working
      // (9 Nov 2026 audit: one crash used to replace the whole app).
      return (
        <div role="alert" style={{ padding: "var(--space-4, 16px)" }}>
          <h2 style={{ margin: "0 0 8px", fontSize: 17 }}>This screen couldn't open</h2>
          <p style={{ margin: "0 0 12px", fontSize: 13.5, color: "var(--text)" }}>
            Something went wrong on this screen. The rest of Vula still works, and the error has
            been reported. Try again, or open another screen from the menu.
          </p>
          <button onClick={() => this.setState({ error: null })} style={{
            padding: "8px 16px", border: "none", borderRadius: 6, background: "var(--accent)",
            color: "var(--on-accent)", fontSize: 13, fontWeight: 600, cursor: "pointer",
          }}>Try again</button>
        </div>
      );
    }
    return (
      <div style={{
        minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
        padding: 24, background: "var(--bg)", color: "var(--ink)",
      }}>
        <div style={{
          maxWidth: 480, background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 12,
          padding: 24, boxShadow: "0 4px 16px rgba(30,30,30,0.08)",
        }}>
          <h2 style={{ margin: "0 0 8px", fontSize: 18 }}>Something went wrong</h2>
          <p style={{ margin: "0 0 12px", fontSize: 13.5, color: "var(--text)" }}>
            This screen hit an unexpected error. Reloading usually fixes it. The error has been
            reported; if it keeps happening, tell your Vula contact what you tapped just before.
          </p>
          <pre style={{
            margin: "0 0 16px", fontSize: 11.5, color: "var(--danger)", background: "var(--surface-alt)",
            padding: 10, borderRadius: 8, overflowX: "auto", whiteSpace: "pre-wrap",
          }}>{String(this.state.error?.message || this.state.error)}</pre>
          <button
            onClick={() => { this.setState({ error: null }); window.location.reload(); }}
            style={{
              padding: "8px 16px", border: "none", borderRadius: 6, background: "var(--accent)",
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
