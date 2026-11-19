// Shared helpers for all pages.

// Normally the backend serves these pages itself (http://127.0.0.1:8000), so API calls
// go to the same address. If a page is opened as a file or from a dev server such as
// VS Code Live Server, talk to the backend on port 8000 directly.
const DEV_SERVER_PORTS = ["5500", "5501", "3000", "5173", "8080"];
const API_BASE = (window.location.protocol === "file:" || DEV_SERVER_PORTS.includes(window.location.port))
    ? "http://127.0.0.1:8000"
    : "";

async function apiFetch(path, options = {}) {
    let response;
    try {
        response = await fetch(API_BASE + path, {
            headers: { "Content-Type": "application/json" },
            ...options,
        });
    } catch (error) {
        throw new Error("Cannot reach the server. Make sure the backend is running.");
    }
    let data = null;
    try { data = await response.json(); } catch (e) { /* empty body */ }
    if (!response.ok) {
        throw new Error((data && data.detail) || "Something went wrong (error " + response.status + ").");
    }
    return data;
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
