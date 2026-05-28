"""Neo4j-backed Redshift Analytics Agent for MTI Brain.

Replaces the SPARQL/Jena + 21-node system with a Neo4j semantic pipeline
and a 10-node LangGraph orchestration layer.

Entry point for the chat router:
    from app.services.agents.pipeline import stream_pipeline
"""
