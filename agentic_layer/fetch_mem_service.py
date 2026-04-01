import db


def retrieve(query: str, query_time=None) -> dict:
    """Retrieve all memory context. No search — everything is included."""
    profile = db.get_user_profile()
    foresight = db.get_active_foresight(query_time) if query_time else []
    summary = db.get_conversation_summary()

    return {
        "profile": profile,
        "foresight": foresight,
        "summary": summary,
    }


def compose_context(result: dict) -> str:
    """Compose memory context from profile + foresight + rolling summary."""
    parts = []

    # Profile
    profile = result.get("profile", "")
    if profile:
        parts.append("=== USER PROFILE ===")
        parts.append(profile)
        parts.append("")

    # Foresight
    foresight = result.get("foresight", [])
    if foresight:
        parts.append("=== UPCOMING / TIME-BOUNDED ===")
        for fs in foresight:
            until = fs.get("valid_until")
            until_str = str(until) if until else "indefinite"
            parts.append(f"- {fs['description']} (valid until: {until_str})")
        parts.append("")

    # Rolling summary
    summary = result.get("summary", {})
    archive = summary.get("archive_text", "")
    recent = summary.get("recent_text", "")

    if archive or recent:
        parts.append("=== CONVERSATION HISTORY ===")
        if archive:
            parts.append("[Archive]")
            parts.append(archive)
            parts.append("")
        if recent:
            parts.append("[Recent]")
            parts.append(recent)

    return "\n".join(parts)
