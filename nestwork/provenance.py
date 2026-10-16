"""C2PA half of the safety net for the agent's face and body photos (see appearance.require_ai): selfies are
generated from them, so they must show an AI-generated character, never a real person. Valid Content Credentials saying a generative AI model made the
image are proof; credentials showing camera or composite content are proof of the opposite (RealEvidence).

The check follows the manifest chain from the active manifest back through its "parentOf" ingredients to the
one that created the image. It passes only when that creation was `trainedAlgorithmicMedia` (fully AI generated)
and nothing along the way is a camera capture, a composite, or another image placed in. Anything else is rejected:
no credentials (stripped, or a generator that doesn't add them), broken credentials (changed after signing),
a photo, or a real photo with AI edits (`compositeWithTrainedAlgorithmicMedia`). Only RealEvidence is final;
the other rejections just mean "no proof", and the vision check decides.

Who signed it matters too (see check): a signer that chains to the C2PA trust list (TRUST_LIST, kept in the repo)
is verified; a valid signature from any other signer (self-signed, like Grok Imagine today) is only a claim, which
appearance.require_ai accepts after the user confirms it.
"""

import functools
import io
import json
from pathlib import Path

import c2pa

IPTC = "http://cv.iptc.org/newscodes/digitalsourcetype/"
AI_CREATED = "trainedAlgorithmicMedia"
# source types allowed on later steps of an AI-made image (e.g. Google marks a format conversion algorithmicallyEnhanced)
OK_EDITS = {AI_CREATED, "humanEdits", "algorithmicallyEnhanced"}
# AI edits on an image: no proof by themselves, but not camera evidence either (see CompositeEvidence)
COMPOSITE = {"compositeWithTrainedAlgorithmicMedia", "compositeSynthetic"}
# The official C2PA trust list (trust anchors, PEM). Without it every signer counts as untrusted.
TRUST_LIST = Path(__file__).with_name("c2pa_trust_list.pem")


class NotAIGenerated(ValueError):
    """No proof of AI generation."""


class RealEvidence(NotAIGenerated):
    """Valid credentials that show real (camera, composite or placed-in) content: rejected outright."""


class CompositeEvidence(RealEvidence):
    """Credentials marking the image as edited with AI (and nothing in it as a camera capture): rejected as an
    original, but an enhanced face or body can still be compared with the anchor."""


def _source(action: dict) -> str:
    t = action.get("digitalSourceType") or ""
    return t.rsplit("/", 1)[-1] if t.startswith(("http://", "https://")) or "/" in t else t


def _proven(manifests: dict, label: str, seen: set, composite: list) -> str:
    """Generator name if this manifest (and every image that went into it) proves AI generation; raise otherwise.
    AI-edit (COMPOSITE) source types are collected in `composite` instead of raising, so the walk still finds a
    camera capture further down the chain."""
    m = manifests.get(label) if label not in seen else None
    if m is None:
        raise NotAIGenerated("its Content Credentials don't say it was created by a generative AI model")
    seen.add(label)
    actions = [a for x in m.get("assertions", []) if x.get("label", "").startswith("c2pa.actions")
               for a in (x.get("data") or {}).get("actions", [])]
    name = None
    for a in actions:
        src = _source(a)
        if a.get("action") == "c2pa.created":
            if src in COMPOSITE:
                composite.append(f"its Content Credentials say it was made as {src}, not by a generative AI model")
            elif src != AI_CREATED:
                raise RealEvidence(f"its Content Credentials say it was made as {src or 'an unspecified source'}, "
                                   "not by a generative AI model")
            else:
                name = ((m.get("claim_generator_info") or [{}])[0].get("name") or m.get("claim_generator")
                        or "an AI generator")
        elif src in COMPOSITE:
            composite.append(f"its Content Credentials say it contains {src} content")
        elif src and src not in OK_EDITS:
            raise RealEvidence(f"its Content Credentials say it contains {src} content")
    # Every image that went in (the previous version, an input reference, a placed-in part) must be proven AI too:
    # otherwise a real photo could be run through an AI editor and come out labelled "AI generated".
    ingredients = m.get("ingredients") or []
    for ing in ingredients:
        if ing.get("relationship") in ("parentOf", "inputTo", "componentOf"):
            if not ing.get("active_manifest"):
                raise NotAIGenerated(f"it was made from another image ({ing.get('relationship')}) that has no Content "
                                     "Credentials, so there's no proof that image was AI generated")
            parent_name = _proven(manifests, ing["active_manifest"], seen, composite)
            name = name or (parent_name if ing.get("relationship") == "parentOf" else None)
    if not name:
        raise NotAIGenerated("its Content Credentials don't say it was created by a generative AI model")
    return name


def verdict(store: dict, state: str) -> str:
    """Name of the AI generator if the manifest store proves an AI-generated image; raise NotAIGenerated otherwise."""
    if state not in ("Valid", "Trusted"):
        raise NotAIGenerated("its Content Credentials don't validate (the image was changed after it was signed)")
    composite = []
    try:
        name = _proven(store.get("manifests") or {}, store.get("active_manifest"), set(), composite)
    except RealEvidence:
        raise
    except NotAIGenerated:
        if composite:
            raise CompositeEvidence(composite[0])
        raise
    if composite:
        raise CompositeEvidence(composite[0])
    return name


def _webp_c2pa_range(raw: bytes) -> tuple[int, int] | None:
    """Where the C2PA chunk's data sits in a WebP file."""
    if raw[:4] != b"RIFF" or raw[8:12] != b"WEBP":
        return None
    i = 12
    while i + 8 <= len(raw):
        n = int.from_bytes(raw[i + 4:i + 8], "little")
        if raw[i:i + 4] == b"C2PA":
            return i + 8, i + 8 + n
        i += 8 + n + (n & 1)
    return None


def _padding_quirk(raw: bytes, mime: str, store: dict, detailed: dict) -> bool:
    """Google Flow's WebP files sign a manifest exclusion one padding byte shorter than the C2PA chunk, which the
    library reports as a data-hash mismatch. Accept that one case only: the claim signature is valid, the only
    failure is that mismatch on the active manifest, the exclusion lies inside the C2PA chunk and misses at most one
    zero byte of it, and the image bytes hash exactly to what was signed."""
    if mime != "image/webp":
        return False
    active = store.get("active_manifest") or ""
    codes = [(s.get("code"), s.get("url") or "") for s in store.get("validation_status") or []]
    if any(c not in ("signingCredential.untrusted", "assertion.dataHash.mismatch") for c, _ in codes) or \
            any(c == "assertion.dataHash.mismatch" and active not in u for c, u in codes):
        return False
    ok = [s.get("code") for s in (((store.get("validation_results") or {}).get("activeManifest") or {}).get("success") or [])]
    if "claimSignature.validated" not in ok:
        return False
    h = ((detailed.get("manifests") or {}).get(active, {}).get("assertion_store") or {}).get("c2pa.hash.data") or {}
    ex, chunk = h.get("exclusions") or [], _webp_c2pa_range(raw)
    if h.get("alg") != "sha256" or len(ex) != 1 or not chunk:
        return False
    s, n = ex[0]["start"], ex[0]["length"]
    if not (s == chunk[0] and 0 <= chunk[1] - (s + n) <= 1 and raw[s + n:chunk[1]].strip(b"\0") == b""):
        return False
    import base64
    import hashlib
    return hashlib.sha256(raw[:s] + raw[s + n:]).digest() == base64.b64decode(h.get("hash") or "")


@functools.cache
def trust_anchors() -> str:
    """PEM certificates whose signers count as trusted: the C2PA trust list, or "" when it isn't there."""
    try:
        return TRUST_LIST.read_text(encoding="utf-8")
    except OSError:
        return ""


def _context() -> "c2pa.Context | None":
    anchors = trust_anchors()
    if not anchors.strip():
        return None
    return c2pa.Context(c2pa.Settings.from_dict({"verify": {"verify_trust": True}, "trust": {"trust_anchors": anchors}}))


def check(raw: bytes, mime: str) -> dict:
    """For an AI-generated image: {"by": generator name, "trusted": the signer chains to the trust list,
    "signer": the signing certificate's common name}. Raise NotAIGenerated (a ValueError) with the reason otherwise."""
    try:
        with c2pa.Reader(mime, io.BytesIO(raw), context=_context()) as r:
            store, state = json.loads(r.json()), str(r.get_validation_state() or "")
            if state == "Invalid" and _padding_quirk(raw, mime, store, json.loads(r.detailed_json())):
                ok = [s.get("code") for s in (((store.get("validation_results") or {}).get("activeManifest") or {})
                                              .get("success") or [])]
                state = "Trusted" if "signingCredential.trusted" in ok else "Valid"
    except c2pa.C2paError:
        raise NotAIGenerated("it has no C2PA Content Credentials")
    name = verdict(store, state)
    sig = ((store.get("manifests") or {}).get(store.get("active_manifest")) or {}).get("signature_info") or {}
    return {"by": name, "trusted": state == "Trusted", "signer": sig.get("common_name") or sig.get("issuer") or "unknown"}
