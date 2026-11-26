const form = document.querySelector("form");
const doiInput = document.getElementById("doi");
const button = document.getElementById("search-button");
const message = document.getElementById("message");

let busy = false;   // a lookup is in progress

function showMessage(text, type) {
    message.innerHTML = '<div class="alert alert-' + type + ' mb-0">' + escapeHtml(text) + "</div>";
}

function resetForm() {
    busy = false;
    button.disabled = false;
    button.textContent = "Get details";
}

// Coming back to this page with the browser's Back button can restore it exactly as it was
// left, with the button still disabled on "Fetching…", so a second lookup would do nothing.
// Reset it whenever the page is shown again.
window.addEventListener("pageshow", function (event) {
    if (event.persisted) {
        resetForm();
        message.innerHTML = "";
    }
});

form.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (busy) return;   // ignore a second click while the first lookup runs
    const doi = doiInput.value.trim();

    // Empty-field validation
    if (doi === "") {
        showMessage("Please enter a DOI.", "warning");
        return;
    }
    // DOI format validation (links like https://doi.org/10.xxx are also accepted)
    const bareDoi = doi.replace(/^(https?:\/\/)?(dx\.)?doi\.org\//i, "").replace(/^doi:\s*/i, "");
    if (!/^10\.\d{4,9}\/\S+$/.test(bareDoi)) {
        showMessage("That doesn't look like a DOI. A DOI starts with 10. — for example 10.1038/nature14539", "warning");
        return;
    }

    // Send the DOI to the backend
    busy = true;
    button.disabled = true;
    button.textContent = "Fetching…";
    showMessage("Looking up the paper… This usually takes 5–30 seconds.", "info");

    try {
        const paper = await apiFetch("/api/papers", {
            method: "POST",
            body: JSON.stringify({ doi: doi, refresh: true }),  // always fetch fresh details
        });
        // Hand the paper to the details page, so it shows exactly this result
        // without asking the server again.
        rememberPaper(paper);
        const note = paper.warning ? "stale" : (paper.already_in_library ? "exists" : "added");
        resetForm();
        window.location.href = "details.html?paperId=" + encodeURIComponent(paper.id) + "&note=" + note;
    } catch (error) {
        // Handles: invalid DOI, DOI not found, sources unreachable, server offline
        showMessage(error.message, "danger");
        resetForm();
    }
});
