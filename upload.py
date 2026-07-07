from fastapi import FastAPI, UploadFile, File
import os
import shutil

app = FastAPI()

UPLOAD_DIR = "/app/data"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    filepath = os.path.join(UPLOAD_DIR, file.filename)

    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return {
        "message": "Uploaded successfully",
        "path": filepath
    }