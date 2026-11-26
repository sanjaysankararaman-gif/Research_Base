// Shared helpers for all pages.

// Normally the backend serves these pages itself (http://127.0.0.1:8000), so API calls
// go to the same address. If a page is opened as a file or from a dev server such as
// VS Code Live Server, talk to the backend on port 8000 directly.
const DEV_SERVER_PORTS = ["5500", "5501", "3000", "5173", "8080"];
const API_BASE = (window.location.protocol === "file:" || DEV_SERVER_PORTS.includes(window.location.port))
    ? "http://127.0.0.1:8000"
    : "";

const REQUEST_TIMEOUT_MS = 150000;   // a lookup normally takes 5-30 s; the server gives up well before this

async function apiFetch(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(function () { controller.abort(); }, REQUEST_TIMEOUT_MS);
    let response;
    try {
        response = await fetch(API_BASE + path, {
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
            signal: controller.signal,
            ...options,
        });
    } catch (error) {
        throw new Error(error.name === "AbortError"
            ? "The server took too long to answer. Please try again."
            : "Cannot reach the server. Make sure the backend is running.");
    } finally {
        clearTimeout(timer);
    }
    let data = null;
    try { data = await response.json(); } catch (e) { /* empty body */ }
    if (!response.ok) {
        throw new Error((data && data.detail) || "Something went wrong (error " + response.status + ").");
    }
    return data;
}

// The Home page passes the paper it just looked up to the details page, so the details page
// shows that exact result even if the server is restarted or another server instance answers.
function rememberPaper(paper) {
    try { sessionStorage.setItem("paper:" + paper.id, JSON.stringify(paper)); } catch (e) { /* storage off */ }
}

function takeRememberedPaper(paperId) {
    try {
        const text = sessionStorage.getItem("paper:" + paperId);
        sessionStorage.removeItem("paper:" + paperId);
        return text ? JSON.parse(text) : null;
    } catch (e) {
        return null;
    }
}

// Escape text before putting it into HTML.
function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// Point every "Download Excel" link at the backend.
document.querySelectorAll("[data-excel-link]").forEach(function (link) {
    link.href = API_BASE + "/api/excel";
});
