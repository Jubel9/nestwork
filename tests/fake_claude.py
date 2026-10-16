"""Stands in for `claude -p ... --output-format stream-json` in tests."""
import json
import os
import sys

args = sys.argv[1:]
prompt = sys.stdin.read()

if "--output-format" in args and args[args.index("--output-format") + 1] == "json":   # photo analysis
    if "MY INTENTION AND EMOTION: goodnight" in prompt and "BROKE A HARD RULE" not in prompt:   # covers up, once
        print(json.dumps({"type": "result", "is_error": False, "result": json.dumps(
            {"prompt": "Lying in bed, blanket pulled up to the chest. Front camera selfie. Right hand stretching off "
                       "screen. Left hand tugging the Sheets. Sleepy smile.", "clothes_to_show": []})}))
        sys.exit(0)
    if "MY INTENTION AND EMOTION" in prompt:   # selfie prompt writer
        print(json.dumps({"type": "result", "is_error": False, "total_cost_usd": 0.001, "result": json.dumps(
            {"prompt": "Sit on the desk edge, wearing this clothes, resize the clothes to match the body reference, "
                       "not the other way around. Front camera selfie. Right hand stretching off screen. Left hand "
                       "tapping a pen. Proud grin.", "include_face": True, "include_room": True, "clothes_to_show": [1]})}))
        sys.exit(0)
    if "SAME CHARACTER CHECK" in prompt:   # enhanced face / body compared with the verified original
        with open(os.environ.get("NESTWORK_FAKE_SAME_LOG", os.devnull), "a") as log:
            log.write(prompt + "\n---\n")
        verdict = {"same": {"same": True, "confidence": 9, "reason": "same eyes and lips"},
                   "small": {"same": True, "confidence": 7, "reason": "same, though the face is small"},
                   "unsure": {"same": True, "confidence": 5, "reason": "hard to tell"},
                   "different": {"same": False, "confidence": 1, "reason": "a different person"},
                   }[os.environ.get("NESTWORK_FAKE_SAME", "same")]
        print(json.dumps({"type": "result", "is_error": False, "result": json.dumps(verdict)}))
        sys.exit(0)
    if "wardrobe" in prompt:
        print(json.dumps({"type": "result", "is_error": False, "total_cost_usd": 0.01, "result": json.dumps(
            {"name": "Lavender knit cardigan", "category": "outerwear", "colors": "lavender", "length": "62 cm",
             "formality": 4, "warmth": 7, "heel_height": None})}))
        sys.exit(0)
    answer = {"face": {"apparent_age": "24 years", "hair_color": "silver", "hair_length": 45, "eye_size": 7,
                       "makeup": 3, "facial_hair": "none"},
              "body": {"height": 168, "weight": "55 kg", "bust": 86, "chest": 99, "build": "slim"},
              "workspace": {"room_width": 350, "screens": 2, "clutter": 2, "style": "cozy"}}
    print(json.dumps({"type": "result", "is_error": False, "result": "Here you go:\n" + json.dumps(answer),
                      "total_cost_usd": 0.01}))
    sys.exit(0)
sid = args[args.index("--resume") + 1] if "--resume" in args else args[args.index("--session-id") + 1]


def out(e):
    print(json.dumps(e), flush=True)


out({"type": "system", "subtype": "init", "session_id": sid})
out({"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "text", "text": ""}}})
out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Let me check. "}}})
out({"type": "stream_event", "event": {"type": "content_block_stop"}})
out({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__nestwork__whats_left", "input": {}}]}})
if "selfie please" in prompt:   # pretend the selfie tool returned a picture
    out({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__nestwork__take_selfie", "input": {}}]}})
    out({"type": "user", "message": {"content": [{"type": "tool_result", "content": [
        {"type": "text", "text": "Selfie sent to the chat. [nestwork-image:/api/generated/x-selfie.png] (Don't paste)"}]}]}})
out({"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "text", "text": ""}}})
out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": f"You said: {prompt}"}}})
out({"type": "stream_event", "event": {"type": "content_block_stop"}})
out({"type": "result", "subtype": "success", "is_error": False, "session_id": sid, "result": "ok",
     "total_cost_usd": 0.01, "duration_ms": 5,
     "usage": {"input_tokens": 10, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 200}})
