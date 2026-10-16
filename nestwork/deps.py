"""FastAPI dependencies shared by main.py and routes_agent.py."""

from fastapi import Request

from . import agent, db, undo


def get_conn(request: Request):
    conn = db.connect()
    try:
        # A change to projects or to-dos is one undo step (⌘Z), labelled from the request.
        label = undo.label_for(conn, request.method, request.url.path) if request.method != "GET" else None
        if label:
            undo.begin(conn, label)
        yield conn
        if label:
            undo.end(conn)
        conn.commit()
    finally:
        conn.close()
    if request.method != "GET":
        # Only reached when the change succeeded and is committed: bump the version open pages
        # poll for and rewrite the Markdown snapshot (data/nestwork.md).
        agent.after_change()
