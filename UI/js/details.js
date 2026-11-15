const params = new URLSearchParams(window.location.search); //gets the query string
const paperId = params.get("paperId"); //gets the paper ID from the query string
console.log(paperId);