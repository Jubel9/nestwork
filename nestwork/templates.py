"""Starter projects.

"after": sibling titles that must be complete before an item can start.
"rules": (target stage, sibling title, required stage), i.e. move this item to the target stage
         once that sibling reaches the required stage.
"""

TEMPLATES = {
    "blank": {
        "name": "Blank project",
        "description": "Start empty and build your own nest.",
        "icon": "□",
        "labels": None,
        "items": [],
    },
    "game": {
        "name": "Game development",
        "description": "Characters, levels and audio, each broken into art and design steps.",
        "icon": "🎮",
        "labels": None,
        "items": [
            {"title": "Characters", "items": [
                {"title": "Cloud", "items": [
                    # The backstory is drafted and reviewed first, but only accepted once the art matches it.
                    {"title": "Backstory", "rules": [("done", "2D sprites", "done"), ("done", "3D model", "done")]},
                    {"title": "2D sprites", "rules": [("ready", "Backstory", "review")]},
                    {"title": "3D model", "rules": [("ready", "2D sprites", "done")]},
                    {"title": "Relations"},
                    {"title": "Character easter egg"},
                ]},
            ]},
            {"title": "Levels", "items": [
                {"title": "Level 1", "items": [
                    {"title": "Layout sketch"},
                    {"title": "Greybox", "after": ["Layout sketch"]},
                    {"title": "Art pass", "after": ["Greybox"]},
                ]},
            ]},
            {"title": "Audio", "items": [
                {"title": "Main theme"},
                {"title": "Sound effects"},
            ]},
            {"title": "Release", "after": ["Characters", "Levels", "Audio"], "items": [
                {"title": "Store page"},
                {"title": "Trailer"},
                {"title": "Launch", "after": ["Store page", "Trailer"]},
            ]},
        ],
    },
    "university": {
        "name": "University application",
        "description": "Tests, documents and submissions, in the order they have to happen.",
        "icon": "🎓",
        "labels": {"backlog": "To do", "ready": "Can start", "in_progress": "Working on it",
                   "review": "Waiting for feedback", "done": "Done"},
        "items": [
            {"title": "Shortlist universities"},
            {"title": "Language test", "items": [
                {"title": "Book the test"},
                {"title": "Practice", "after": ["Book the test"]},
                {"title": "Take the test", "after": ["Practice"]},
            ]},
            {"title": "Documents", "items": [
                {"title": "Transcript"},
                {"title": "Recommendation letters"},
                {"title": "Personal statement", "items": [
                    {"title": "Brainstorm"},
                    {"title": "First draft", "after": ["Brainstorm"]},
                    {"title": "Get feedback", "after": ["First draft"]},
                    {"title": "Final version", "after": ["Get feedback"]},
                ]},
            ]},
            {"title": "Submit applications",
             "after": ["Shortlist universities", "Language test", "Documents"]},
            {"title": "Visa & housing", "after": ["Submit applications"]},
        ],
    },
    "jobs": {
        "name": "Job hunt",
        "description": "Prepare once, then track every application through its interview rounds.",
        "icon": "💼",
        "labels": {"backlog": "Wishlist", "ready": "Ready to go", "in_progress": "In progress",
                   "review": "Waiting to hear back", "done": "Done"},
        "items": [
            {"title": "Preparation", "items": [
                {"title": "Update CV"},
                {"title": "Portfolio"},
                {"title": "Cover letter template", "after": ["Update CV"]},
            ]},
            {"title": "Applications", "after": ["Preparation"], "items": [
                {"title": "Example Company", "items": [
                    {"title": "Apply"},
                    {"title": "Phone screen", "after": ["Apply"]},
                    {"title": "Interview", "after": ["Phone screen"]},
                    {"title": "Offer", "after": ["Interview"]},
                ]},
            ]},
        ],
    },
    "study": {
        "name": "Study course",
        "description": "Modules with reading, exercises and a quiz, leading up to the final exam.",
        "icon": "📚",
        "labels": {"backlog": "Not started", "ready": "Up next", "in_progress": "Studying",
                   "review": "Revising", "done": "Mastered"},
        "items": [
            {"title": f"Module {n}", **({"after": [f"Module {n - 1}"]} if n > 1 else {}), "items": [
                {"title": "Read the material"},
                {"title": "Exercises", "after": ["Read the material"]},
                {"title": "Quiz", "after": ["Exercises"]},
            ]}
            for n in (1, 2, 3)
        ] + [{"title": "Final exam", "after": ["Module 1", "Module 2", "Module 3"]}],
    },
    "work": {
        "name": "Work project",
        "description": "Discovery to launch, each phase waiting on the one before.",
        "icon": "🏢",
        "labels": None,
        "items": [
            {"title": "Discovery", "items": [
                {"title": "Stakeholder interviews"},
                {"title": "Requirements", "after": ["Stakeholder interviews"]},
            ]},
            {"title": "Design", "after": ["Discovery"]},
            {"title": "Build", "after": ["Design"]},
            {"title": "Launch", "after": ["Build"]},
        ],
    },
}


def summaries() -> list[dict]:
    return [
        {"key": k, "name": t["name"], "description": t["description"], "icon": t["icon"]}
        for k, t in TEMPLATES.items()
    ]
