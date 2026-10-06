import React from "react";
import ReactDOM from "react-dom/client";
import PayApp from "./PayApp";

ReactDOM.createRoot(document.getElementById("root")).render(<PayApp />);

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/pay/sw.js", { scope: "/pay/" }).catch(() => {});
  });
}
