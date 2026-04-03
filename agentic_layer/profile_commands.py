import re
import db

## TODO: A better approach would be to have a LLM call for this 

_REMEMBER_PATTERNS = [
    re.compile(r"(?:please\s+)?remember\s+(?:that\s+)?(.+)", re.IGNORECASE),
    re.compile(r"(?:please\s+)?(?:note|save|store)\s+(?:that\s+)?(.+)", re.IGNORECASE),
    re.compile(r"(?:yaad|yaad\s+rakh|yaad\s+rakhna)\s+(?:ki\s+)?(.+)", re.IGNORECASE),
]

_FORGET_PATTERNS = [
    re.compile(r"(?:please\s+)?forget\s+(?:that\s+)?(.+)", re.IGNORECASE),
    re.compile(r"(?:please\s+)?(?:remove|delete)\s+(?:that\s+)?(.+)", re.IGNORECASE),
    re.compile(r"(?:bhool\s+ja|bhul\s+ja)\s+(?:ki\s+)?(.+)", re.IGNORECASE),
]


def detect_command(message: str) -> dict | None:
    """Detect remember/forget command in message.

    Returns: {"type": "remember"|"forget", "content": str} or None
    """
    message = message.strip()
    for pattern in _REMEMBER_PATTERNS:
        match = pattern.match(message)
        if match:
            return {"type": "remember", "content": match.group(1).strip()}
    for pattern in _FORGET_PATTERNS:
        match = pattern.match(message)
        if match:
            return {"type": "forget", "content": match.group(1).strip()}
    return None


def execute_remember(content: str) -> str:
    """Add a fact directly to the user profile."""
    profile = db.get_user_profile()
    fact_line = f"- {content}"

    if profile:
        # Check if already exists (case-insensitive)
        if content.lower() in profile.lower():
            return f"Yeh toh already yaad hai mujhe: {content}"
        profile = profile.rstrip() + "\n" + fact_line
    else:
        profile = fact_line

    db.upsert_user_profile(profile)
    return f"Added to the profile: {content}"


def execute_forget(content: str) -> str:
    """Remove a fact from the user profile."""
    profile = db.get_user_profile()
    if not profile:
        return "Profile doesn't have anything."

    lines = profile.split("\n")
    content_lower = content.lower()
    new_lines = []
    removed = False

    for line in lines:
        if content_lower in line.lower():
            removed = True
            continue
        new_lines.append(line)

    if not removed:
        return f"Profile doesn't have this: {content}"

    db.upsert_user_profile("\n".join(new_lines))
    return f"Removed from the profile: {content}"


def handle_command(message: str) -> str | None:
    """Detect and handle profile command. Returns response string or None if not a command."""
    cmd = detect_command(message)
    if cmd is None:
        return None
    if cmd["type"] == "remember":
        return execute_remember(cmd["content"])
    else:
        return execute_forget(cmd["content"])
