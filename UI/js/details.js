const params = new URLSearchParams(window.location.search); // gets the query string
const paperId = params.get("paperId");                      // gets the paper ID from the query string
const note = params.get("note");                            // "added" or "exists" when coming from Home

// The 23 fields of "Essential Fields for Research Publication Database", in order,
// followed by fields 24-27 (date and institution) added by the department.
// [number, label, key in the backend response]
const SECTIONS = [
    {
        title: "1. Common Fields (Journal and Conference)",
        applies: null,
        fields: [
            [1, "Publication Title", "title"],
            [2, "Publication Type (Journal / Conference)", "publication_type"],
            [3, "Author Names (in published order)", "authors_text"],
            [4, "Publication Year", "publication_year"],
            [5, "Publisher", "publisher"],
            [6, "DOI", "doi"],
            [7, "Publication URL", "publication_url"],
            [8, "Indexing Database (Scopus / Web of Science / Other)", "indexing"],
        ],
    },
    {
        title: "2. Journal-Specific Fields",
        applies: "Journal",
        fields: [
            [9, "Journal Name", "journal"],
            [10, "ISSN / E-ISSN", "issn"],
            [11, "Volume", "volume"],
            [12, "Issue", "issue"],
            [13, "Page Range / Article Number", "page_range"],
            [14, "Quartile (Q1 / Q2 / Q3 / Q4 / N/A)", "quartile"],
            [15, "Impact Factor", "impact_factor"],
        ],
    },
    {
        title: "3. Conference-Specific Fields",
        applies: "Conference",
        fields: [
            [16, "Conference Name", "conference_name"],
            [17, "Conference Acronym", "conference_acronym"],
            [18, "Conference Dates", "conference_dates"],
            [19, "Conference Location", "conference_location"],
            [20, "Proceedings Title", "proceedings_title"],
            [21, "Proceedings Publisher", "proceedings_publisher"],
            [22, "Proceedings ISBN / ISSN", "proceedings_isbn_issn"],
            [23, "Proceedings Volume / Page Range", "proceedings_volume_pages"],
        ],
    },
    {
        title: "4. Date and Institution",
        applies: null,
        fields: [
            [24, "Publication Date", "publication_date"],
            [25, "Home Institution", "home_institution"],
            [26, "Home Faculty (Faculty ID)", "home_faculty"],
            [27, "Author Affiliations", "author_affiliations"],
        ],
    },
];

const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December"];

// "2015-05-28" -> "28 May 2015"; "2015-05" -> "May 2015"; "2015" -> "2015"
function formatDate(value) {
    const parts = String(value).split("-");
    const month = MONTHS[Number(parts[1]) - 1];
    if (parts.length === 3 && month) return Number(parts[2]) + " " + month + " " + parts[0];
    if (parts.length === 2 && month) return month + " " + parts[0];
    return String(value);
}

function isEmpty(value) {
    return value === null || value === undefined || value === "" || (Array.isArray(value) && value.length === 0);
}

function valueHtml(key, value, fields) {
    if (key === "publication_url" || key === "doi") {
        const url = key === "doi" ? "https://doi.org/" + value : value;
        return '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener">' + escapeHtml(value) + "</a>";
    }
    if (key === "publication_date") {
        return '<time datetime="' + escapeHtml(value) + '">' + escapeHtml(formatDate(value)) + "</time>";
    }
    if (key === "quartile" && fields.quartile_detail) {
        return escapeHtml(value) + '<div class="reason">' + escapeHtml(fields.quartile_detail) + "</div>";
    }
    if (key === "home_institution") {
        return '<span class="badge text-bg-success me-1">Yes</span>' + escapeHtml(value);
    }
    if (key === "home_faculty") {
        // [{faculty_id, name, author, position, evidence}]
        return '<ul class="list-unstyled mb-0">' + value.map(function (f) {
            const nameOnly = /^name only/.test(f.evidence);
            return "<li>" + escapeHtml(f.name) + ' <span class="faculty-id">' + escapeHtml(f.faculty_id) + "</span>" +
                '<div class="reason">' + (f.position ? "Author " + escapeHtml(f.position) : "Author") +
                (f.author !== f.name ? ' (written "' + escapeHtml(f.author) + '")' : "") +
                (nameOnly ? ", matched by name only: no source lists this author's affiliation" : "") +
                "</div></li>";
        }).join("") + "</ul>";
    }
    if (key === "author_affiliations") {
        // [{author, institutions, is_home}] in published order
        return '<ul class="list-unstyled mb-0 affiliations">' + value.map(function (a) {
            return "<li" + (a.is_home ? ' class="home"' : "") + ">" +
                '<span class="author">' + escapeHtml(a.author) + "</span>: " +
                (a.institutions.length ? escapeHtml(a.institutions.join("; ")) : '<span class="not-available">not listed</span>') +
                (a.is_home ? ' <span class="badge text-bg-success">Home</span>' : "") + "</li>";
        }).join("") + "</ul>";
    }
    return escapeHtml(value);
}

// Fields 25 and 26 are answered "No" when the authors were checked and none is from the
// home institution; that is an answer, not a missing value.
function answeredNo(key, fields) {
    return (key === "home_institution" || key === "home_faculty") && fields.institutions_checked;
}

function showBanner(text, type) {
    document.getElementById("banner").innerHTML =
        '<div class="alert alert-' + type + '">' + escapeHtml(text) + "</div>";
}

// Short explanation of why a field is empty, from the backend's reason.
function shortReason(reason) {
    if (!reason) return "Not provided by any source for this paper.";
    if (/SJR_CSV_PATH/.test(reason)) return "SCImago ranking file not set up.";
    if (/Clarivate|licensed|IMPACT_FACTOR/i.test(reason)) return "Impact Factor is licensed by Clarivate; not in any free source.";
    if (/not found in the SCImago/i.test(reason)) return "This journal is not ranked by SCImago.";
    if (/No ISSN/i.test(reason)) return "No ISSN, so it cannot be ranked.";
    if (/preprint server/i.test(reason)) return "Published version has no DOI, so no source has this.";
    if (/saved before institutions/i.test(reason)) return reason;
    if (/affiliation/i.test(reason)) return "No source lists the authors' affiliations for this paper.";
    return "Not provided by any source for this paper.";
}

const PROVENANCE_KEY = { authors_text: "authors" };

function showPaper(paper) {
    const f = paper.fields;
    const type = f.publication_type;

    document.getElementById("publication-type").textContent = type || "Publication";
    document.getElementById("paper-title").textContent = f.title || "Untitled";
    document.title = (f.title || "Paper") + " | Research base";

    let filled = 0;
    let applicable = 0;
    const rows = [];

    SECTIONS.forEach(function (section) {
        // A journal paper has no conference fields (and vice versa): say so instead of "missing".
        const notApplicable = section.applies && (type === "Journal" || type === "Conference") && type !== section.applies;
        rows.push('<tr class="section-row"><th colspan="3">' + escapeHtml(section.title) +
            (notApplicable ? ' <span class="fw-normal text-secondary">(not applicable: this is a ' +
                escapeHtml(type.toLowerCase()) + " paper)</span>" : "") + "</th></tr>");

        section.fields.forEach(function (field) {
            const number = field[0], label = field[1], key = field[2];
            const value = f[key];
            let cell;
            if (notApplicable) {
                cell = '<span class="text-secondary">—</span>';
            } else {
                applicable += 1;
                const prov = (paper.provenance || {})[PROVENANCE_KEY[key] || key] || {};
                if (isEmpty(value) && answeredNo(key, f)) {
                    filled += 1;
                    cell = "No" + '<div class="reason">' + escapeHtml(prov.reason || "") + "</div>";
                } else if (isEmpty(value)) {
                    cell = '<span class="not-available">Not available</span>' +
                        '<div class="reason">' + escapeHtml(shortReason(prov.reason)) + "</div>";
                } else {
                    filled += 1;
                    cell = valueHtml(key, value, f);
                }
            }
            rows.push("<tr" + (notApplicable ? ' class="na-row"' : "") + ">" +
                '<td class="field-no">' + number + "</td>" +
                '<td class="field-name">' + escapeHtml(label) + "</td>" +
                '<td class="field-value">' + cell + "</td></tr>");
        });
    });

    document.getElementById("fields-body").innerHTML = rows.join("");
    document.getElementById("fill-summary").textContent =
        filled + " of " + applicable + " fields found for this " + (type ? type.toLowerCase() + " paper" : "paper") + ".";

    if (f.version_note) {
        document.getElementById("version-note").innerHTML =
            '<div class="alert alert-warning">' + escapeHtml(f.version_note) + "</div>";
    }

    // PDF handling
    const pdfLink = document.getElementById("pdf-link");
    const pdfStatus = document.getElementById("pdf-status");
    if (f.pdf_url) {
        pdfStatus.textContent = "PDF availability: Free PDF available";
        pdfLink.href = f.pdf_url;
        pdfLink.hidden = false;
    } else {
        pdfStatus.textContent = f.pdf_available === false
            ? "PDF availability: No free PDF (the paper is behind a paywall)"
            : "PDF availability: No free PDF found";
        pdfLink.hidden = true;
    }
}

function showNote() {
    if (note === "added") showBanner("Paper saved to your library and to the Excel file.", "success");
    if (note === "exists") showBanner("This paper was already in your library. Its details were fetched again and updated in the Excel file.", "info");
    if (note === "stale") showBanner("The academic sources could not be reached just now, so these are the details saved earlier. Look the DOI up again in a minute to refresh them.", "warning");
}

async function loadPaper() {
    if (!paperId) {
        showBanner("No paper selected. Choose a paper from My Library.", "warning");
        document.getElementById("paper-title").textContent = "No paper selected";
        document.getElementById("fields").hidden = true;
        document.getElementById("pdf-info").hidden = true;
        return;
    }
    // Just looked up on the Home page: show that exact result.
    const remembered = takeRememberedPaper(paperId);
    if (remembered) {
        showPaper(remembered);
        showNote();
        return;
    }
    try {
        const paper = await apiFetch("/api/papers/" + encodeURIComponent(paperId));
        showPaper(paper);
        showNote();
    } catch (error) {
        // Handles missing paper IDs, unavailable records and failed requests
        showBanner(error.message, "danger");
        document.getElementById("paper-title").textContent = "Paper not found";
        document.getElementById("publication-type").hidden = true;
        document.getElementById("fields").hidden = true;
        document.getElementById("pdf-info").hidden = true;
    }
}

loadPaper();
