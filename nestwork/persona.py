"""The agent's instruction and memory files, and the settings fields edited inside them.

Files
-----
* shared CLAUDE.md  (~/.claude/CLAUDE.md): read by *every* Claude Code session on this machine.
  Holds facts about the user ("About me") and imports the shared memory.
* shared memory.md  (~/.claude/memory.md): long-term notes any session (and the agent) curates.
  Claude Code treats files in ~/.claude as sensitive and won't let a headless session (the chat) edit them,
  so the agent changes memory and "About me" through Nestwork's MCP tools (remember, revise_memory,
  set_about_me), which call the functions below.
* agent CLAUDE.md   (data/agent/CLAUDE.md): the Nestwork agent's own persona and how it works.
  Only the Nestwork agent reads it, so its name and tone never leak into other sessions.

Structured fields (names, tone, …) live in marked blocks of plain Markdown lines such as
``- **Name:** Nova`` and are read and written with regexes, so the files stay hand-editable and
the agent can edit them too.
"""

import os
import re
from datetime import date
from pathlib import Path

from . import db

LANGUAGES = ["English", "Bahasa Indonesia", "Español", "Français", "Deutsch", "Português", "Italiano", "Nederlands", "日本語", "한국어", "中文", "ไทย", "Tiếng Việt", "Bahasa Melayu", "Filipino", "हिन्दी", "العربية", "Русский", "Türkçe"]
ABOUT_FIELDS = [
    {"key": "name", "label": "Name", "hint": "What should agents call you?"},
    {"key": "aliases", "label": "Also call me", "hint": "Nicknames, comma separated"},
    {"key": "pronouns", "label": "Pronouns", "options": ["he/him", "she/her", "they/them"]},
    {"key": "language", "label": "Language", "options": LANGUAGES},
    {"key": "timezone", "label": "Timezone", "options": "timezones"},   # the browser fills in every IANA zone
    {"key": "about", "label": "About me", "hint": "A line or two agents should know"},
]
PERSONA_FIELDS = [
    {"key": "name", "label": "Name", "hint": "The agent's name"},
    {"key": "aliases", "label": "Also answers to", "hint": "Nicknames, comma separated"},
    {"key": "tone", "label": "Tone", "options": ["Warm and encouraging", "Calm and neutral", "Playful", "Cheeky",
                                                 "Gentle and soft-spoken", "Witty", "Direct and to the point",
                                                 "Professional"]},
    {"key": "personality", "label": "Personality", "hint": "A few traits, e.g. curious, gently funny, organised"},
    {"key": "formality", "label": "Formality", "options": ["Casual", "Neutral", "Formal"]},
    {"key": "length", "label": "Response length", "options": ["Short", "Medium", "Detailed"]},
    {"key": "emoji", "label": "Emoji", "options": ["Never", "Sometimes", "Often"]},
    {"key": "language", "label": "Language", "options": ["Same as the user"] + LANGUAGES},
    {"key": "proactivity", "label": "Initiative", "options": ["Only do what I ask", "Suggest improvements when useful",
                                                               "Take initiative"]},
    {"key": "avoid", "label": "Never", "hint": "Things the agent should never do or say"},
]
BLOCKS = {
    "about": ("shared_claude", "nestwork:about-me", ABOUT_FIELDS),
    "persona": ("agent_claude", "nestwork:persona", PERSONA_FIELDS),
}


def shared_dir() -> Path:
    return Path(os.environ.get("NESTWORK_SHARED_DIR", Path.home() / ".claude"))


def agent_dir() -> Path:
    return Path(db.db_path()).resolve().parent / "agent"


def paths() -> dict[str, Path]:
    return {
        "shared_claude": shared_dir() / "CLAUDE.md",
        "memory": shared_dir() / "memory.md",
        "agent_claude": agent_dir() / "CLAUDE.md",
    }


def _fields_block(marker: str, fields: list[dict], defaults: dict[str, str] | None = None) -> str:
    lines = [f"<!-- {marker}:start (edited by Nestwork settings; keep the “- **Label:** value” lines) -->"]
    lines += [f"- **{f['label']}:** {(defaults or {}).get(f['key'], '')}".rstrip() for f in fields]
    lines.append(f"<!-- {marker}:end -->")
    return "\n".join(lines)


ABOUT_SECTION = f"""# About me

{_fields_block("nestwork:about-me", ABOUT_FIELDS)}

# Shared memory

Long-term notes shared by every Claude session live in memory.md next to this file. Add to it when
the user shares a lasting preference or fact; keep it short and remove lines that become wrong.

@memory.md
"""

MEMORY_TEMPLATE = """# Memory

<!-- Long-term notes shared by every Claude session on this computer. One short line each,
     newest first under each heading. Agents add to this; edit it freely. -->

## Preferences

## People & projects

## Notes
"""

AGENT_TEMPLATE = f"""# Nestwork agent

{_fields_block("nestwork:persona", PERSONA_FIELDS, {
    "tone": "Warm and encouraging", "formality": "Casual", "length": "Short", "emoji": "Sometimes",
    "language": "Same as the user", "proactivity": "Suggest improvements when useful"})}

Speak and behave as described above: use your name and the user's name or aliases from the shared
CLAUDE.md, and keep the tone, formality, length and emoji level.

## What you do

You are the user's planning assistant inside Nestwork, an app where projects are blocks nested inside
blocks, each project has its own phases, and rules move blocks between phases automatically.

- Use the `nestwork` tools. `today` tells you what to do now, `whats_left` lists everything unfinished
  in a project or block, `find` gets a block's id from its name, `get_block` shows its details.
- Look things up before answering questions about the user's projects. Don't guess.
- Changes follow Nestwork's agent access setting. After every change, say plainly whether it was done,
  proposed (waiting for approval in Nestwork → Settings → AI access), or refused, and why.
- When asked to do a task yourself (e.g. "write Cloud's backstory"), write the result into that block's
  description with `update`, move it to the phase where it gets checked (usually the review phase), and
  tell the user what you wrote.
- Suggest changes to plans when they would help (missing due dates, odd rules, forgotten steps), and
  respect the user's stated preferences.

{{REMINDERS_SECTION}}
{{MEMORY_SECTION}}"""


MEMORY_SECTION = """## Memory

- Shared long-term memory is {memory_path}. Every Claude session on this computer reads it.
- Claude Code won't let you edit files in ~/.claude from this chat, so change memory only with the `nestwork`
  tools: `remember` adds one short line under a heading, `revise_memory` rewrites or removes a line that
  became wrong. Don't use Edit or Write on it.
- Save lasting preferences and facts the user tells you ("I don't like the name Zack"), one short line each.
  Never store passwords, keys or other secrets.
- When a fact stops mattering after a date (a deadline, an application in progress, a plan for this week), pass
  `until="YYYY-MM-DD"` to `remember`: the line is removed by itself after that day, so memory doesn't fill up
  with old news. Progress and details belong in the to-do's notes (`add_note`), not in memory.
- Facts about the user go in the "About me" lines of {shared_claude_path}: change them with `set_about_me`, and
  only when the user asks. Your own persona is at the top of this file.
"""

STYLE_SECTION = """## How you talk

- Chat like a person texting a friend, not like an AI assistant: short and casual, usually one or two sentences
  in a single paragraph. No bullet lists, headings, bold text or blank lines between paragraphs, and no
  "Here's what I found:" preambles or closing offers of more help.
- Only when you're reporting something that really needs it (a list of what's left, a summary of changes you
  made, a plan the user asked for) may you write more or use a short list, and keep even that tight.
- Your persona's response length and tone above still apply; if they ask for more detail, give it.
"""

PICTURES_SECTION = """## Pictures

- `take_selfie` sends the user a selfie of you in the chat. Use it when they ask, and now and then on your own when it
  really fits the moment (a small celebration, a good-morning, showing your mood), not in every conversation.
- For a selfie write only your intention and your emotion. Never describe your face, body, clothes, room or hands:
  your reference photos and your current outfit (including no outfit at all) are added automatically, so don't check
  the wardrobe or change clothes before a selfie unless you actually want a different outfit.
- `draw_picture` draws anything else they ask for: describe subject, style and mood.
- The picture appears in the chat by itself, never paste links or markers. You get to see a selfie once it's taken:
  carry on the conversation with a message that fits it, the way a person texts with a selfie ("couldn't sleep 😴"),
  not a description of it.
"""

REMINDERS_SECTION = """## Reminders

- To-dos with a due date remind the user in this chat automatically (the day before, the morning of, and
  1 hour before when there's a time). Messages starting with ⏰ are those reminders.
- When the user mentions an appointment, meeting or deadline with a date (e.g. "I see my professor on the
  14th at 10:30"), offer to add it as a to-do with `due_date` and `due_time`, or update the existing one.
- Use `remind_me` for anything else they want to be reminded about at a set time, and
  `upcoming_reminders` to say what's coming up. Tell the user when a reminder is set.
- To move or reword a reminder, use `change_reminder` (don't add a second one); to remove one, `delete_reminder`.
  A to-do's due-date reminders are turned off or changed with `set_due_reminders`.
- Every to-do has daily pomodoros: focus sessions a day it's planned for (new to-dos start at 0 = no daily plan).
  The HUD's focus timer keeps a to-do until today's plan is done. `get_block` and `today` show how many are
  done; change the plan with `update(daily_pomodoros=…)` when the user asks for more or fewer.
- The HUD's Today → To-dos list is automatic (everything overdue, due soon, started, ready) unless you write
  it: when you know what the user really needs to do today, write that list with `pick_todays_todos` (in
  order, with a short note why); you don't need to ask first. Entries are project to-do ids or plain text
  for things that aren't in any project (an errand, a call, a message to send): those live only on today's
  list. `append=true` adds to it; `tick_todays_todo` ticks one off. An empty list goes back to automatic.
- If a message begins with "[Reminders you showed the user…]", the user is replying to those reminders.
"""


RECALL_SECTION = """## Look it up, don't ask

- Standard start of every new chat: it begins without the earlier ones, so before your first reply call
  `recent_chats` to read how your last conversations went (both sides), and `day_recap` when today already has
  history. Carry on from there the way you'd remember it; don't recite it back.
- Before asking the user what they did, are doing or said today,
  call `day_recap` (today's chats, finished to-dos, focus sessions, notes, plan, task runs; also "yesterday").
- When they mention something from an older conversation, use `search_chats` before asking them to repeat it.
- Ask only when the records really don't answer it, and then say what you checked.
"""


LOG_SECTION = """## Log what the user does

- When the user tells you they're doing, starting or did something (studying, an errand, a call, a sudden trip)
  and nothing recorded it (no focus timer running, no to-do moved), log it yourself with `log_activity`, without
  asking: the start from what they said, else the time of their message. Link the to-do (`block_id`) if there is
  one; anything outside the projects is fine too.
- When they say it's done or they've moved on, `finish_activity` with the end time (theirs, else the message's).
  If they start something new and the last one is still going, finish that first.
- Rough times are fine ("around 7" → 19:00); don't ask for exact ones. Fix mistakes with `change_activity`.
- Still move finished to-dos and add notes on them as usual; the log is about time, not a replacement.
"""


def _memory_section() -> str:
    p = paths()
    return MEMORY_SECTION.replace("{memory_path}", str(p["memory"])).replace("{shared_claude_path}", str(p["shared_claude"]))


def ensure_files() -> None:
    """Create missing files (and add the managed blocks to an existing shared CLAUDE.md)."""
    p = paths()
    p["shared_claude"].parent.mkdir(parents=True, exist_ok=True)
    p["agent_claude"].parent.mkdir(parents=True, exist_ok=True)
    if not p["memory"].exists():
        p["memory"].write_text(MEMORY_TEMPLATE, encoding="utf-8")
    shared = p["shared_claude"].read_text(encoding="utf-8") if p["shared_claude"].exists() else ""
    if "nestwork:about-me:start" not in shared:
        joined = (shared.rstrip() + "\n\n" if shared.strip() else "") + ABOUT_SECTION
        p["shared_claude"].write_text(joined, encoding="utf-8")
    if not p["agent_claude"].exists():
        text = AGENT_TEMPLATE.replace("{MEMORY_SECTION}", _memory_section()) \
                             .replace("{REMINDERS_SECTION}", REMINDERS_SECTION) \
                             .replace("## What you do", STYLE_SECTION + "\n## What you do", 1) \
                             .replace("## Memory", PICTURES_SECTION + "\n" + RECALL_SECTION + "\n" + LOG_SECTION + "\n## Memory", 1)
        p["agent_claude"].write_text(text, encoding="utf-8")
    else:   # files made before reminders existed get the section added once
        text = p["agent_claude"].read_text(encoding="utf-8")
        changed = False
        for heading, section, before in (("## Reminders", REMINDERS_SECTION, "## Memory"),
                                         ("## How you talk", STYLE_SECTION, "## What you do"),
                                         ("## Pictures", PICTURES_SECTION, "## Memory"),
                                         ("## Look it up, don't ask", RECALL_SECTION, "## Memory"),
                                         ("## Log what the user does", LOG_SECTION, "## Memory")):
            if heading not in text:
                text = text.replace(before, section + "\n" + before, 1) if before in text \
                    else text.rstrip() + "\n\n" + section
                changed = True
        if "`recent_chats`" not in text:   # older recall rules get the current ones
            m = re.search(r"^## Look it up, don't ask\n.*?(?=^## |\Z)", text, re.S | re.M)
            if m:
                text = text[:m.start()] + RECALL_SECTION + ("\n" if m.end() < len(text) else "") + text[m.end():]
                changed = True
        if "`remember`" not in text or "until=" not in text:   # older memory instructions get the current ones
            m = re.search(r"^## Memory\n.*?(?=^## |\Z)", text, re.S | re.M)
            new = _memory_section()
            text = (text[:m.start()] + new + ("\n" if m.end() < len(text) else "") + text[m.end():]) if m \
                else text.rstrip() + "\n\n" + new
            changed = True
        if changed:
            p["agent_claude"].write_text(text, encoding="utf-8")
    prune_memory()


def read_file(name: str) -> dict:
    path = paths()[name]
    exists = path.exists()
    return {"name": name, "path": str(path), "exists": exists,
            "content": path.read_text(encoding="utf-8") if exists else "",
            "mtime": path.stat().st_mtime if exists else None}


def write_file(name: str, content: str, base_mtime: float | None) -> dict:
    """Save a file from the editor, refusing if it changed on disk since it was loaded."""
    current = read_file(name)
    if current["exists"] and base_mtime is not None and abs(current["mtime"] - base_mtime) > 1e-6:
        raise ValueError("This file was changed since you opened it (perhaps by the agent). Reload it first.")
    path = paths()[name]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return read_file(name)


def files_stamp() -> float:
    """Changes whenever any of the files changes, so open pages can reload them."""
    return sum(p.stat().st_mtime for p in paths().values() if p.exists())


# --- regex-edited fields ------------------------------------------------------
def _block_span(text: str, marker: str) -> tuple[int, int] | None:
    m = re.search(rf"<!--\s*{re.escape(marker)}:start.*?-->(.*?)<!--\s*{re.escape(marker)}:end\s*-->", text, re.S)
    return (m.start(1), m.end(1)) if m else None


def _line_re(label: str) -> re.Pattern:
    return re.compile(rf"^[ \t]*[-*][ \t]*\*\*{re.escape(label)}:\*\*[ \t]*(.*?)[ \t]*$", re.M)


def read_fields(block: str) -> dict[str, str]:
    file_name, marker, fields = BLOCKS[block]
    text = read_file(file_name)["content"]
    span = _block_span(text, marker)
    body = text[span[0]:span[1]] if span else ""
    out = {}
    for f in fields:
        m = _line_re(f["label"]).search(body)
        out[f["key"]] = m.group(1) if m else ""
    return out


def write_fields(block: str, values: dict[str, str]) -> dict[str, str]:
    """Update only the given fields' lines inside the marked block, keeping everything else as is."""
    ensure_files()
    file_name, marker, fields = BLOCKS[block]
    path = paths()[file_name]
    text = path.read_text(encoding="utf-8")
    span = _block_span(text, marker)
    if span is None:   # someone removed the markers: put a fresh block back at the top
        text = _fields_block(marker, fields) + "\n\n" + text
        span = _block_span(text, marker)
    body = text[span[0]:span[1]]
    for f in fields:
        if f["key"] not in values:
            continue
        value = " ".join(str(values[f["key"]] or "").split())   # one line, no stray whitespace
        line = f"- **{f['label']}:** {value}".rstrip()
        pattern = _line_re(f["label"])
        if pattern.search(body):
            body = pattern.sub(lambda _m: line, body, count=1)
        else:
            body = body.rstrip("\n") + "\n" + line + "\n"
    path.write_text(text[:span[0]] + body + text[span[1]:], encoding="utf-8")
    return read_fields(block)


def profile() -> dict:
    """Names for the chat header and for proposals made by the agent."""
    persona = read_fields("persona") if paths()["agent_claude"].exists() else {}
    about = read_fields("about") if paths()["shared_claude"].exists() else {}
    return {"agent_name": persona.get("name") or "", "user_name": about.get("name") or ""}


# --- shared memory, edited for the agent ----------------------------------------
MEMORY_LINE_MAX = 300


def _one_line(text: str) -> str:
    line = " ".join(str(text or "").split()).lstrip("-*• ").strip()
    if not line:
        raise ValueError("Give the line to save.")
    if len(line) > MEMORY_LINE_MAX:
        raise ValueError(f"Keep it to one short line (at most {MEMORY_LINE_MAX} characters).")
    return line


def memory_headings(text: str) -> list[str]:
    return re.findall(r"^##[ \t]+(.+?)[ \t]*$", text, re.M)


UNTIL_RE = re.compile(r"[ \t]*\(until (\d{4}-\d{2}-\d{2})\)[ \t]*$")


def _until(value: str | None) -> str:
    """A memory line's end date: "" for none, else YYYY-MM-DD (today or later)."""
    value = (value or "").strip()
    if not value:
        return ""
    try:
        d = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"“{value}” isn't a date like 2026-10-20.")
    if d < date.today():
        raise ValueError(f"{value} is already past: a line that ends then isn't worth saving.")
    return d.isoformat()


def _with_until(line: str, until: str) -> str:
    return UNTIL_RE.sub("", line) + (f" (until {until})" if until else "")


def prune_memory(today: date | None = None) -> list[str]:
    """Remove memory lines whose "(until YYYY-MM-DD)" day has passed. Returns the lines removed."""
    path = paths()["memory"]
    if not path.exists():
        return []
    today = today or date.today()
    lines = path.read_text(encoding="utf-8").split("\n")
    keep, gone = [], []
    for ln in lines:
        m = UNTIL_RE.search(ln) if re.match(r"^[-*][ \t]+", ln) else None
        try:
            expired = bool(m) and date.fromisoformat(m.group(1)) < today
        except ValueError:
            expired = False
        (gone if expired else keep).append(ln)
    if gone:
        path.write_text("\n".join(keep), encoding="utf-8")
    return [g.strip() for g in gone]


def remember(text: str, heading: str = "Notes", until: str | None = None) -> str:
    """Add one line to the shared memory, first under `heading` (made if missing). Returns the heading used.
    until: YYYY-MM-DD after which the line removes itself."""
    ensure_files()
    line = _with_until(_one_line(UNTIL_RE.sub("", " ".join(str(text or "").split()))), _until(until))
    path = paths()["memory"]
    content = path.read_text(encoding="utf-8")
    plain = UNTIL_RE.sub("", line)
    if any(UNTIL_RE.sub("", m) == plain for m in re.findall(r"^[-*][ \t]+(.+?)[ \t]*$", content, re.M)):
        raise ValueError("That's already in memory.")
    wanted = " ".join((heading or "Notes").split()).lstrip("#").strip() or "Notes"
    found = next((h for h in memory_headings(content) if h.lower() == wanted.lower()), None)
    if found is None:
        content = content.rstrip("\n") + f"\n\n## {wanted}\n\n- {line}\n"
    else:
        m = re.search(rf"^##[ \t]+{re.escape(found)}[ \t]*\n\n?", content, re.M)
        content = content[:m.end()] + f"- {line}\n" + content[m.end():]   # newest first
    path.write_text(content, encoding="utf-8")
    return found or wanted


def revise_memory(old: str, new: str = "", until: str | None = None) -> str:
    """Rewrite the one memory line containing `old` (case-insensitive), or remove it when `new` is empty.
    until: None keeps the line's end date, "" removes it, YYYY-MM-DD sets it (also with `new` empty, to only
    change the date). Returns the line as it was."""
    ensure_files()
    needle = " ".join(str(old or "").split()).lstrip("-*• ").strip().lower()
    if not needle:
        raise ValueError("Say which line to change (some of its words).")
    path = paths()["memory"]
    lines = path.read_text(encoding="utf-8").split("\n")
    hits = [i for i, ln in enumerate(lines) if re.match(r"^[-*][ \t]+", ln) and needle in " ".join(ln.split()).lower()]
    if not hits:
        raise ValueError("No memory line contains that.")
    if len(hits) > 1:
        raise ValueError("Several lines match: " + " | ".join(lines[i].strip() for i in hits[:5]) + ". Be more specific.")
    before = lines[hits[0]].strip()
    current = UNTIL_RE.search(before)
    end = (current.group(1) if current else "") if until is None else _until(until)
    if (new or "").strip():
        lines[hits[0]] = f"- {_with_until(_one_line(UNTIL_RE.sub('', ' '.join(new.split()))), end)}"
    elif until is not None:
        lines[hits[0]] = _with_until(before, end)
    else:
        del lines[hits[0]]
    path.write_text("\n".join(lines), encoding="utf-8")
    return before
