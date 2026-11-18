const form = document.querySelector("form");
const doiInput = document.getElementById("doi");
const button = document.getElementById("search-button");
const message = document.getElementById("message");

function showMessage(text, type) {
    message.innerHTML = '<div class="alert alert-' + type + ' mb-0">' + escapeHtml(text) + "</div>";
}

form.addEventListener("submit", async function (event) {
    event.preventDefault();
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
    button.disabled = true;
    button.textContent = "Fetching…";
    showMessage("Looking up the paper… This usually takes 5–30 seconds.", "info");

    try {
        const paper = await apiFetch("/api/papers", {
            method: "POST",
            body: JSON.stringify({ doi: doi, refresh: true }),  // always fetch fresh details
        });
        // Go to the details page for this paper
        const note = paper.already_in_library ? "exists" : "added";
        window.location.href = "details.html?paperId=" + paper.id + "&note=" + note;
    } catch (error) {
        // Handles: invalid DOI, DOI not found, sources unreachable, server offline
        showMessage(error.message, "danger");
        button.disabled = false;
        button.textContent = "Get details";
    }
});
