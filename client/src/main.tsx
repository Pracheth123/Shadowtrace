import React from "react";
import ReactDOM from "react-dom/client";

import App from "@/App";
import "./styles.css";
// Homepage motion/interaction styles; after the base sheet so they refine it.
import "./components/home/home.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
