from fastapi import FastAPI

from app.models import Event, ExtractRequest

app = FastAPI(title="Atlas backend")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/extract")
def extract(request: ExtractRequest) -> list[Event]:
    return [
        Event(
            title="Team sync",
            date_phrase="next Thursday at 3pm",
            source_excerpt="Let's meet next Thursday at 3pm to sync on the launch.",
            confidence="medium",
            ambiguities=["year not specified"],
        )
    ]
