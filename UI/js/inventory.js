const searchInput = document.getElementById("search-input");
const searchButton = document.getElementById("search-button");
const publicationFilter = document.getElementById("publication-filter"); // the type of publication
const paperList = document.getElementById("paper-list");                 // table body containing research papers
const paperCount = document.getElementById("paper-count");

function shorten(text, max) {
    if (!text) return "";
    return text.length > max ? text.slice(0, max) + "…" : text;
}

// "Home" badge for papers with an author from the home institution; hover shows the faculty IDs.
function homeBadge(paper) {
    if (!paper.home_institution) return "";
    const faculty = (paper.home_faculty || []).map(function (f) { return f.name + " (" + f.faculty_id + ")"; });
    const tip = paper.home_institution + (faculty.length ? ": " + faculty.join(", ") : "");
    return ' <span class="badge text-bg-success home-badge" title="' + escapeHtml(tip) + '">Home</span>';
}

function showRows(papers) {
    if (papers.length === 0) {
        const filtered = searchInput.value.trim() || publicationFilter.value;
        paperList.innerHTML = '<tr><td colspan="7" class="text-center text-secondary py-4">' +
            (filtered ? "No papers match your search." : 'No papers yet. <a href="index.html">Add one with a DOI</a>.') +
            "</td></tr>";
        return;
    }
    paperList.innerHTML = papers.map(function (paper, index) {
        return "<tr>" +
            "<td>" + (index + 1) + "</td>" +
            "<td>" + escapeHtml(paper.title || "Untitled") + homeBadge(paper) + "</td>" +
            "<td>" + escapeHtml(shorten(paper.authors_text, 80)) + "</td>" +
            "<td>" + escapeHtml(paper.publication_year || "") + "</td>" +
            "<td>" + escapeHtml(paper.publication_type || "") + "</td>" +
            '<td class="doi-cell">' + escapeHtml(paper.doi) + "</td>" +
            '<td><a class="btn btn-sm btn-outline-primary" href="details.html?paperId=' + paper.id + '">View</a></td>' +
            "</tr>";
    }).join("");
}

async function loadPapers() {
    const query = new URLSearchParams({ page_size: "200", sort: "id", order: "asc" });
    const searchValue = searchInput.value.trim();     // entered value in the search bar
    const selectedType = publicationFilter.value;     // selected publication type from the dropdown
    if (searchValue) query.set("search", searchValue);
    if (selectedType) query.set("type", selectedType);

    try {
        const data = await apiFetch("/api/papers?" + query.toString());
        showRows(data.items);
        paperCount.textContent = data.total === 1 ? "1 paper" : data.total + " papers";
    } catch (error) {
        paperList.innerHTML = '<tr><td colspan="7" class="text-center text-danger py-4">' +
            escapeHtml(error.message) + "</td></tr>";
        paperCount.textContent = "";
    }
}

// main
searchButton.addEventListener("click", loadPapers);
publicationFilter.addEventListener("change", loadPapers);
searchInput.addEventListener("keydown", function (event) {
    if (event.key === "Enter") loadPapers();
});
loadPapers();
