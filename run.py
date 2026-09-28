import uvicorn

if __name__ == "__main__":
    uvicorn.run("personal_rail.app:app", host="127.0.0.1", port=2036, access_log=False)
