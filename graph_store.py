from neo4j import GraphDatabase
from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

_driver = None


def get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    return _driver


def close_driver():
    global _driver
    if _driver:
        _driver.close()
        _driver = None


def init_graph():
    """Create indexes for fast entity lookup."""
    driver = get_driver()
    with driver.session() as session:
        # Index on entity name for fast lookup
        session.run("CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)")
        session.run("CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)")


def clear_graph():
    """Delete all nodes and relationships."""
    driver = get_driver()
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")


def find_entity(name: str, threshold: float = 0.85) -> dict | None:
    """Find an entity by exact name match (case-insensitive)."""
    driver = get_driver()
    with driver.session() as session:
        result = session.run(
            "MATCH (e:Entity) WHERE toLower(e.name) = toLower($name) RETURN e",
            name=name
        )
        record = result.single()
        if record:
            node = record["e"]
            return {"id": node.element_id, "name": node["name"], "type": node.get("type", "")}
    return None


def find_or_create_entity(name: str, entity_type: str = "", conversation_date: str = None) -> dict:
    """Find existing entity or create new one."""
    existing = find_entity(name)
    if existing:
        return existing

    driver = get_driver()
    with driver.session() as session:
        result = session.run(
            """
            CREATE (e:Entity {name: $name, type: $type, created_at: $date})
            RETURN e
            """,
            name=name, type=entity_type, date=conversation_date or ""
        )
        node = result.single()["e"]
        return {"id": node.element_id, "name": node["name"], "type": node.get("type", "")}


def create_edge(source_name: str, relation: str, target_name: str,
                conversation_date: str = None, is_valid: bool = True) -> None:
    """Create a relationship edge between two entities."""
    driver = get_driver()
    with driver.session() as session:
        session.run(
            """
            MATCH (s:Entity {name: $source})
            MATCH (t:Entity {name: $target})
            CREATE (s)-[:RELATES {type: $relation, conversation_date: $date, is_valid: $valid}]->(t)
            """,
            source=source_name, target=target_name,
            relation=relation, date=conversation_date or "", valid=is_valid
        )


def invalidate_edges(source_name: str, relation_type: str) -> int:
    """Mark existing edges of a specific type as invalid (soft delete)."""
    driver = get_driver()
    with driver.session() as session:
        result = session.run(
            """
            MATCH (s:Entity {name: $source})-[r:RELATES {type: $relation, is_valid: true}]->()
            SET r.is_valid = false
            RETURN count(r) as invalidated
            """,
            source=source_name, relation=relation_type
        )
        return result.single()["invalidated"]


def get_entity_relationships(entity_name: str, max_depth: int = 2,
                              valid_only: bool = True) -> list[dict]:
    """Traverse graph from an entity node, returning connected relationships.

    Args:
        entity_name: Starting entity name
        max_depth: How many hops to traverse (1 = direct, 2 = friends of friends)
        valid_only: Only return edges where is_valid=true
    """
    driver = get_driver()
    with driver.session() as session:
        valid_filter = "AND r.is_valid = true" if valid_only else ""

        if max_depth == 1:
            query = f"""
                MATCH (s:Entity)-[r:RELATES]->(t:Entity)
                WHERE toLower(s.name) = toLower($name) {valid_filter}
                RETURN s.name as source, r.type as relation, t.name as target,
                       t.type as target_type, r.conversation_date as date
                UNION
                MATCH (s:Entity)<-[r:RELATES]-(t:Entity)
                WHERE toLower(s.name) = toLower($name) {valid_filter}
                RETURN t.name as source, r.type as relation, s.name as target,
                       s.type as target_type, r.conversation_date as date
            """
        else:
            query = f"""
                MATCH path = (s:Entity)-[r:RELATES*1..{max_depth}]-(t:Entity)
                WHERE toLower(s.name) = toLower($name)
                UNWIND relationships(path) as rel
                WITH startNode(rel) as src, rel, endNode(rel) as tgt
                WHERE rel.is_valid = true OR NOT $valid_only
                RETURN src.name as source, rel.type as relation, tgt.name as target,
                       tgt.type as target_type, rel.conversation_date as date
            """

        result = session.run(query, name=entity_name, valid_only=valid_only)
        relationships = []
        seen = set()
        for record in result:
            key = (record["source"], record["relation"], record["target"])
            if key not in seen:
                seen.add(key)
                relationships.append({
                    "source": record["source"],
                    "relation": record["relation"],
                    "target": record["target"],
                    "target_type": record.get("target_type", ""),
                    "date": record.get("date", ""),
                })
        return relationships


def upsert_relationship(source: str, source_type: str, relation: str,
                         target: str, target_type: str, conversation_date: str = None) -> None:
    """Full pipeline: find/create entities, check for conflicts, create edge."""
    # Find or create both entities
    find_or_create_entity(source, source_type, conversation_date)
    find_or_create_entity(target, target_type, conversation_date)

    # For mutually exclusive relations (lives_in, works_at), invalidate old edges
    exclusive_relations = {"lives_in", "works_at", "studies_at", "relationship_status"}
    if relation.lower().replace(" ", "_") in exclusive_relations:
        invalidated = invalidate_edges(source, relation)
        if invalidated > 0:
            print(f"    [graph] Invalidated {invalidated} old '{relation}' edges for {source}")

    # Create new edge
    create_edge(source, relation, target, conversation_date)


def search_graph_for_query(query: str, max_depth: int = 2) -> list[dict]:
    """Extract entity names from query and traverse graph.
    Returns list of relationship dicts for context composition."""
    # Simple entity extraction: find any entity name that appears in the query
    driver = get_driver()
    with driver.session() as session:
        # Get all entity names
        result = session.run("MATCH (e:Entity) RETURN e.name as name")
        all_entities = [r["name"] for r in result]

    # Find which entities appear in the query (case-insensitive)
    query_lower = query.lower()
    matched_entities = [e for e in all_entities if e.lower() in query_lower]

    if not matched_entities:
        return []

    # Traverse from each matched entity
    all_relationships = []
    seen = set()
    for entity in matched_entities:
        rels = get_entity_relationships(entity, max_depth=max_depth, valid_only=True)
        for r in rels:
            key = (r["source"], r["relation"], r["target"])
            if key not in seen:
                seen.add(key)
                all_relationships.append(r)

    return all_relationships


def get_graph_stats() -> dict:
    """Return node and edge counts."""
    driver = get_driver()
    with driver.session() as session:
        nodes = session.run("MATCH (n) RETURN count(n) as count").single()["count"]
        edges = session.run("MATCH ()-[r]->() RETURN count(r) as count").single()["count"]
        valid_edges = session.run(
            "MATCH ()-[r:RELATES {is_valid: true}]->() RETURN count(r) as count"
        ).single()["count"]
        return {"nodes": nodes, "edges": edges, "valid_edges": valid_edges}
