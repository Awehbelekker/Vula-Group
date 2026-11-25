import React from "react";
import ReactDOM from "react-dom/client";
import FlowApp from "./FlowApp";

ReactDOM.createRoot(document.getElementById("root")).render(<FlowApp />);

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/flow/sw.js", { scope: "/flow/" }).catch(() => {});
  });
}
