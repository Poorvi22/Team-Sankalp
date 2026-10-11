"""FastAPI app: auth, generated REST routes, streaming agent endpoint, static UI."""
import json
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import agent, auth, db, llm, ops, seed

WEB = db.ROOT / "web"
@asynccontextmanager
async def lifespan(_app):
    if not db.DB_PATH.exists():
        print("No database found - seeding 24 months of synthetic data ...")
        seed.seed()
    db.get_db()
    from . import agentkit, nlu
    agentkit.metadata_index()     # build the retrieval index and entity lexicon once at start-up, not on the first user request
    nlu.lexicon()
    yield


app = FastAPI(title="BizPilot AI", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)


def current_user(request: Request):
    email = auth.read_token(request.cookies.get(auth.COOKIE))
    user = db.q1("SELECT * FROM users WHERE email=?", (email,)) if email else None
    if not user:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user


@app.post("/api/auth/login")
async def login(request: Request, response: Response):
    body = await request.json()
    user = db.q1("SELECT * FROM users WHERE lower(email)=lower(?)", (body.get("email", ""),))
    if not user or not auth.verify_password(body.get("password", ""), user["salt"], user["pw_hash"]):
        return JSONResponse({"error": "Invalid email or password."}, status_code=401)
    response.set_cookie(auth.COOKIE, auth.make_token(user["email"]), httponly=True, samesite="lax", max_age=auth.SESSION_TTL)
    return {"email": user["email"], "name": user["name"], "role": user["role"]}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


@app.get("/api/auth/me")
def me(user=Depends(current_user)):
    return {k: user[k] for k in ("email", "name", "role", "approval_limit")}


# ---- agent -----------------------------------------------------------------------------------------------------
def _ndjson(gen):
    for ev in gen:
        yield json.dumps(ev, default=str) + "\n"


@app.post("/api/agent/chat")
async def agent_chat(request: Request, user=Depends(current_user)):
    body = await request.json()
    return StreamingResponse(_ndjson(agent.run_turn(user, body.get("message", ""), body.get("context") or {})), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/agent/confirm")
async def agent_confirm(request: Request, user=Depends(current_user)):
    body = await request.json()
    return StreamingResponse(_ndjson(agent.confirm(user, body.get("token", ""), body.get("decision", "cancel"))), media_type="application/x-ndjson")


@app.get("/api/agent/status")
def agent_status(user=Depends(current_user)):
    return {"llm_enabled": llm.enabled(), "llm_model": llm.MODEL if llm.enabled() else None, "tiers": ["fast", "llm" if llm.enabled() else None, "fallback"]}


@app.post("/api/agent/eval")
def agent_eval(user=Depends(current_user)):
    """Run the offline benchmark in a subprocess against a copy of the database (never touches live data)."""
    proc = subprocess.run([sys.executable, "-m", "eval.run_eval", "--quiet"], cwd=str(db.ROOT), capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        return JSONResponse({"error": (proc.stderr or proc.stdout)[-600:]}, status_code=500)
    return agent.last_eval() or {}


ops.register_routes(app, current_user)


@app.exception_handler(HTTPException)
async def _http_exc(request, exc):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})
