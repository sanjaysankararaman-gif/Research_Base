const form = document.querySelector("form");

form.addEventListener("submit", 
        function(event) 
        {
        event.preventDefault();
        const doi = document.getElementById("doi").value.trim();

    if (doi === "") {
        alert("Please enter a DOI");
    }
    else if (!/^10\.\d{4,9}\/\S+$/.test(doi)) {
        alert("Please enter a valid DOI");
    }
    else {
        alert("DOI entered successfully: " + doi);
    }

});