"""Runnable starter projects for MIND Builder.

Each template is a complete, working application with a preview command and a
test command. The AI coding agent edits these files; it does not start from
nothing, which keeps generated projects runnable inside the offline sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProjectTemplate:
    key: str
    title: str
    description: str
    runtime: str
    preview_command: tuple[str, ...]
    test_command: tuple[str, ...]
    port: int
    files: dict[str, str] = field(default_factory=dict)


_STATIC_INDEX = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>My MIND App</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <main class="card">
    <h1 id="title">My MIND App</h1>
    <p>Edit these files in MIND Builder, or describe a change in the chat.</p>
    <form id="todo-form">
      <input id="todo-input" placeholder="Add a task" required>
      <button type="submit">Add</button>
    </form>
    <ul id="todo-list"></ul>
  </main>
  <script src="app.js"></script>
</body>
</html>
"""

_STATIC_CSS = """:root { color-scheme: light dark; font-family: system-ui, sans-serif; }
body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #0f1020; color: #eef; }
.card { width: min(32rem, 92vw); padding: 2rem; border-radius: 1rem; background: #1a1c33; box-shadow: 0 10px 40px #0006; }
form { display: flex; gap: .5rem; }
input { flex: 1; padding: .6rem .8rem; border-radius: .5rem; border: 1px solid #3a3d66; background: #11132a; color: inherit; }
button { padding: .6rem 1rem; border: 0; border-radius: .5rem; background: #6c5ce7; color: white; cursor: pointer; }
li { padding: .4rem 0; border-bottom: 1px solid #2a2d4d; }
"""

_STATIC_JS = """const form = document.getElementById('todo-form');
const input = document.getElementById('todo-input');
const list = document.getElementById('todo-list');
const KEY = 'mind-todos';
const load = () => { try { return JSON.parse(localStorage.getItem(KEY) || '[]'); } catch { return []; } };
const save = (items) => { try { localStorage.setItem(KEY, JSON.stringify(items)); } catch {} };
function render() {
  list.innerHTML = '';
  for (const t of load()) { const li = document.createElement('li'); li.textContent = t; list.appendChild(li); }
}
form.addEventListener('submit', (e) => { e.preventDefault(); save([...load(), input.value]); input.value = ''; render(); });
render();
"""

_STATIC_TEST = """from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Refs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for key in ("src", "href"):
            if a.get(key) and not a[key].startswith(("http", "#", "data:", "mailto:")):
                self.refs.append(a[key])


def test_index_exists_and_local_assets_resolve():
    html = (ROOT / "index.html").read_text()
    assert "<title>" in html
    p = Refs()
    p.feed(html)
    missing = [r for r in p.refs if not (ROOT / r).exists()]
    assert not missing, f"missing assets: {missing}"
"""

_FASTAPI_MAIN = '''"""A small notes API with a browser UI. SQLite file lives in the workspace."""

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

DB = Path(__file__).with_name("app.db")
app = FastAPI(title="My MIND API")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL)")
    return conn


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(Path(__file__).with_name("index.html"))


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/notes")
def list_notes() -> list[dict]:
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT id, text FROM notes ORDER BY id")]


@app.post("/api/notes", status_code=201)
def create_note(note: NoteIn) -> dict:
    with db() as conn:
        cur = conn.execute("INSERT INTO notes (text) VALUES (?)", (note.text,))
        return {"id": cur.lastrowid, "text": note.text}


@app.delete("/api/notes/{note_id}", status_code=204)
def delete_note(note_id: int) -> None:
    with db() as conn:
        if conn.execute("DELETE FROM notes WHERE id = ?", (note_id,)).rowcount == 0:
            raise HTTPException(404, "note not found")
'''

_FASTAPI_INDEX = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>My MIND API</title>
<style>body{font-family:system-ui;max-width:36rem;margin:3rem auto;padding:0 1rem}li{margin:.3rem 0}</style></head>
<body>
<h1>Notes</h1>
<form id="f"><input id="t" required placeholder="New note"> <button>Add</button></form>
<ul id="list"></ul>
<script>
async function load(){const r=await fetch('api/notes');const items=await r.json();
  document.getElementById('list').innerHTML='';
  for(const n of items){const li=document.createElement('li');li.textContent=n.text;document.getElementById('list').appendChild(li);}}
document.getElementById('f').onsubmit=async(e)=>{e.preventDefault();const t=document.getElementById('t');
  await fetch('api/notes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:t.value})});t.value='';load();};
load();
</script>
</body></html>
"""

_FASTAPI_TEST = """import os
import tempfile

os.chdir(tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from pathlib import Path  # noqa: E402

main.DB = Path(tempfile.mkdtemp()) / "test.db"
client = TestClient(main.app)


def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}


def test_create_list_delete():
    created = client.post("/api/notes", json={"text": "hello"}).json()
    assert created["text"] == "hello"
    assert any(n["id"] == created["id"] for n in client.get("/api/notes").json())
    assert client.delete(f"/api/notes/{created['id']}").status_code == 204
    assert client.delete(f"/api/notes/{created['id']}").status_code == 404


def test_validation():
    assert client.post("/api/notes", json={"text": ""}).status_code == 422
"""

_NODE_SERVER = """// Dependency-free Node.js server (the sandbox has no network for npm install).
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

const items = [];

function handler(req, res) {
  if (req.url === '/api/items' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify(items));
  }
  if (req.url === '/api/items' && req.method === 'POST') {
    let body = '';
    req.on('data', (c) => { body += c; if (body.length > 1e5) req.destroy(); });
    req.on('end', () => {
      try {
        const { name } = JSON.parse(body || '{}');
        if (!name) throw new Error('name required');
        const item = { id: items.length + 1, name: String(name) };
        items.push(item);
        res.writeHead(201, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(item));
      } catch (e) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }
  if (req.url === '/' || req.url === '/index.html') {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    return res.end(fs.readFileSync(path.join(__dirname, 'index.html')));
  }
  res.writeHead(404, { 'Content-Type': 'text/plain' });
  res.end('not found');
}

module.exports = { handler, items };

if (require.main === module) {
  const port = Number(process.env.PORT || 3000);
  http.createServer(handler).listen(port, '0.0.0.0', () => console.log(`listening on ${port}`));
}
"""

_NODE_INDEX = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>My MIND Node App</title></head>
<body style="font-family:system-ui;max-width:36rem;margin:3rem auto">
<h1>Items</h1>
<form id="f"><input id="n" required> <button>Add</button></form>
<ul id="l"></ul>
<script>
async function load(){const r=await fetch('api/items');const l=document.getElementById('l');l.innerHTML='';
 for(const i of await r.json()){const li=document.createElement('li');li.textContent=i.name;l.appendChild(li);}}
document.getElementById('f').onsubmit=async(e)=>{e.preventDefault();const n=document.getElementById('n');
 await fetch('api/items',{method:'POST',body:JSON.stringify({name:n.value})});n.value='';load();};
load();
</script></body></html>
"""

_NODE_TEST = """const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const { handler } = require('../server.js');

test('create and list items', async () => {
  const server = http.createServer(handler).listen(0);
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const created = await fetch(`${base}/api/items`, { method: 'POST', body: JSON.stringify({ name: 'a' }) });
    assert.strictEqual(created.status, 201);
    const list = await (await fetch(`${base}/api/items`)).json();
    assert.ok(list.some((i) => i.name === 'a'));
    const bad = await fetch(`${base}/api/items`, { method: 'POST', body: '{}' });
    assert.strictEqual(bad.status, 400);
  } finally {
    server.close();
  }
});
"""

PROJECT_TEMPLATES: dict[str, ProjectTemplate] = {
    t.key: t
    for t in (
        ProjectTemplate(
            "static-web",
            "Static web app",
            "HTML, CSS and JavaScript single-page app with local storage.",
            "python",
            ("python", "-m", "http.server", "8000", "--bind", "0.0.0.0"),  # noqa: S104 - inside the sandbox container
            ("python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"),
            8000,
            {
                "index.html": _STATIC_INDEX,
                "styles.css": _STATIC_CSS,
                "app.js": _STATIC_JS,
                "tests/test_site.py": _STATIC_TEST,
            },
        ),
        ProjectTemplate(
            "python-fastapi",
            "Python FastAPI app",
            "FastAPI JSON API with SQLite storage and a browser UI.",
            "python",
            ("uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"),  # noqa: S104 - inside the sandbox container
            ("python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"),
            8000,
            {
                "main.py": _FASTAPI_MAIN,
                "index.html": _FASTAPI_INDEX,
                "tests/test_main.py": _FASTAPI_TEST,
                "tests/conftest.py": "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))\n",
            },
        ),
        ProjectTemplate(
            "node-http",
            "Node.js app",
            "Dependency-free Node.js HTTP server with a JSON API and UI.",
            "node",
            ("node", "server.js"),
            ("node", "--test"),
            3000,
            {"server.js": _NODE_SERVER, "index.html": _NODE_INDEX, "tests/server.test.js": _NODE_TEST},
        ),
    )
}
