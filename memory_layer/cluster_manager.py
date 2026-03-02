import json
from google import genai
from config import GEMINI_API_KEY, GEMINI_MODEL, SCENE_SIMILARITY_THRESHOLD
from models import MemScene
import db
import vector_store
from agentic_layer.vectorize_service import embed_text

client = genai.Client(api_key=GEMINI_API_KEY)


def _generate_scene_label_and_summary(episode_text: str) -> dict:
    """Ask Gemini to generate a theme label and summary for a new scene."""
    prompt = f"""Given this episode, generate a short theme label (2-4 words) and a one-sentence summary for the memory scene it belongs to.

Episode: {episode_text}

Output as JSON:
```json
{{"theme_label": "...", "summary": "..."}}
```
Return ONLY the JSON."""

    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
    return json.loads(text)


def _update_scene_summary(scene_id: int, new_episode: str) -> str:
    """Update an existing scene's summary incorporating the new episode."""
    cells = db.get_memcells_by_scene(scene_id)
    existing_episodes = [c["episode_text"] for c in cells]

    prompt = f"""A memory scene has these existing episodes:
{chr(10).join(f'- {ep}' for ep in existing_episodes)}

A new episode has been added:
- {new_episode}

Write an updated one-sentence summary that captures the overall theme of this scene.

Return ONLY the summary text, nothing else."""

    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return response.text.strip()


def assign_to_scene(memcell_id: int, episode_text: str,
                     episode_embedding: list[float] | None = None,
                     scene_hint: dict | None = None) -> int:
    """
    Assign a MemCell to an existing or new MemScene.

    1. Embed the episode text (or use pre-computed embedding if provided)
    2. Find nearest scene centroid in Qdrant
    3. If similarity > threshold: assign to that scene, update summary
    4. Else: create a new scene (use scene_hint if available to skip LLM call)

    Returns the scene_id.
    """
    if episode_embedding is None:
        episode_embedding = embed_text(episode_text)

    # Search for nearest existing scene
    nearest = vector_store.search_nearest_scene(episode_embedding, top_k=1)

    if nearest and nearest[0]["score"] >= SCENE_SIMILARITY_THRESHOLD:
        # Assign to existing scene
        scene_id = nearest[0]["memscene_id"]
        db.update_memcell_scene(memcell_id, scene_id)

        # Update scene summary
        new_summary = _update_scene_summary(scene_id, episode_text)
        db.update_memscene_summary(scene_id, new_summary)

        # Update scene centroid (simple average: re-embed the updated summary)
        centroid_embedding = embed_text(new_summary)
        vector_store.upsert_scene(scene_id, centroid_embedding)

        return scene_id
    else:
        # Create new scene — use pre-computed hint if available
        if scene_hint:
            scene_info = scene_hint
        else:
            scene_info = _generate_scene_label_and_summary(episode_text)
        scene = MemScene(
            theme_label=scene_info["theme_label"],
            summary=scene_info["summary"],
        )
        scene_id = db.insert_memscene(scene)
        db.update_memcell_scene(memcell_id, scene_id)

        # Store scene centroid
        vector_store.upsert_scene(scene_id, episode_embedding)

        return scene_id
