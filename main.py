from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel


app = FastAPI()

templates = Jinja2Templates(directory = "templates")

@app.post('/api/papers')
async def save_paper(paper: PublicationIn):



@app.get('/api/lookup')
async def lookup(doi: str):


@app.post("/save")
async def save(doi: str = Form(), publication_title : str = Form()):
    