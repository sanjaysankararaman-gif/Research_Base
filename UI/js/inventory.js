const searchInput = document.getElementById("search-input");
const searchButton = document.getElementById("search-button");
const publicationFilter = document.getElementById("publication-filter");//the typr of publication
const paperList = document.getElementById("paper-list");//Table body containing research papers

function filterPapers() 
    {
    const searchValue = searchInput.value.toLowerCase();//entered value in the search bar
    const selectedType = publicationFilter.value;//selected publication type from the dropdown
    const rows = paperList.getElementsByTagName("tr");//all the rows in the table body

    for (let i = 0; i < rows.length; i++) {

        const row = rows[i];

        const paperDetails = row.textContent.toLowerCase();
        const publicationType = row.cells[4].textContent; //retrieving the publication type

        //boolean values
        const matchesSearch = paperDetails.includes(searchValue);//checking if the entered value is present anywhere in the row
        const matchesFilter = selectedType === "" || publicationType === selectedType; //checking if the selected publication type matches the publication type of the row

        if (matchesSearch && matchesFilter) {
            row.style.display = "";
        } else {
            row.style.display = "none";
        }
    }
}

//main
searchButton.addEventListener("click", filterPapers);
publicationFilter.addEventListener("change", filterPapers);