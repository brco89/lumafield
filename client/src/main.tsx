import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";

// No StrictMode: double-invoked effects would open two microphone sessions
// with the voice SDK during development.
createRoot(document.getElementById("root")!).render(<App />);
